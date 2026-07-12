from __future__ import annotations

import itertools
import hashlib
import os
import queue
import re
import stat
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, cast

from xhbx_rag.atomic_indexer import (
    AtomicIndexResult,
    AtomicJournalIdentity,
    RollbackPendingError,
    UntrustedJournalError,
)
from xhbx_rag.web.ingestion_pipeline import PreparedIngestion
from xhbx_rag.web.ingestion_store import IngestionStore, RecoveryAction
from xhbx_rag.web.ingestion_uploads import clear_attempt_workspaces, delete_job_workspace
from xhbx_rag.web.safe_errors import ingestion_exception_error, safe_ingestion_error


class _Pipeline(Protocol):
    def prepare(
        self,
        job: Mapping[str, object],
        attempt_dir: Path,
        *,
        on_event: Callable[[str, dict[str, object]], None] | None = None,
    ) -> PreparedIngestion: ...


class _AtomicIndexer(Protocol):
    def commit(
        self,
        chunks_path: Path,
        *,
        journal_dir: Path,
        identity: AtomicJournalIdentity | None = None,
        on_state: Callable[[str, Path], None] | None = None,
    ) -> AtomicIndexResult: ...

    def recover(
        self, journal_path: Path, *, expected_identity: AtomicJournalIdentity | None = None
    ) -> None: ...

    def inspect_journal_state(
        self, journal_path: Path, *, expected_identity: AtomicJournalIdentity | None = None
    ) -> str: ...


QueueKind = Literal["job", "recovery", "interrupt", "delete", "stop"]


class _UntrustedRecoveryMaterial(RuntimeError):
    pass


@dataclass(frozen=True)
class _ExpectedPaths:
    job_dir: Path
    attempts_dir: Path
    attempt_no: int
    workspace: Path
    rollback_dir: Path
    journal: Path
    backup: Path


@dataclass(order=True, frozen=True)
class _QueueItem:
    priority: int
    sequence: int
    kind: QueueKind = field(compare=False)
    job_id: str = field(compare=False)


class IngestionRunner:
    """单线程串行执行入库任务，并按 durable journal 恢复提交窗口。"""

    def __init__(
        self,
        *,
        store: IngestionStore,
        pipeline: _Pipeline,
        indexer_factory: Callable[[str], _AtomicIndexer],
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.store = store
        self.pipeline = pipeline
        self.indexer_factory = indexer_factory
        self._sleep_fn = sleep_fn
        self._queue: queue.PriorityQueue[_QueueItem] = queue.PriorityQueue()
        self._sequence = itertools.count()
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_enqueued = False

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._stop_enqueued = False
            self._thread = threading.Thread(
                target=self._worker,
                name="xhbx-ingestion-runner",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_event.set()
            thread = self._thread
            if (
                thread is not None
                and thread.is_alive()
                and not self._stop_enqueued
            ):
                self._stop_enqueued = True
                self._put("stop", "", priority=-100)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10.0)
        with self._lifecycle_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def enqueue(self, job_id: str) -> None:
        self._put("job", str(job_id), priority=10)

    def execute_job(self, job_id: str) -> None:
        if not self.store.claim_job(job_id):
            return
        job = self.store.get_job_for_execution(job_id)
        if job is None:
            return
        try:
            paths = _expected_paths(job)
            _validate_attempt_binding(job, paths, allow_workspace_none=True)
            _validate_directory_chain(paths, include_rollback=False)
            workspace = paths.workspace
            workspace.mkdir(parents=True, exist_ok=False)
            self.store.begin_attempt(job_id, workspace)
        except Exception as exc:
            self._finish_precommit_failure(job_id, job, exc)
            return

        try:
            self.store.mark_stage(job_id, "parsing", "")
            prepared = self.pipeline.prepare(
                job,
                workspace,
                on_event=lambda event, payload: self._on_pipeline_event(
                    job_id, job, event, payload
                ),
            )
        except Exception as exc:
            self._fail_active_items(job_id, job, exc)
            self._finish_precommit_failure(job_id, job, exc)
            return

        indexer: _AtomicIndexer | None = None
        try:
            self.store.mark_stage(job_id, "indexing", "")
            content_sha256 = hashlib.sha256(prepared.chunks_path.read_bytes()).hexdigest()
            self.store.mark_content_identity(job_id, content_sha256)
            identity = AtomicJournalIdentity(
                job_id, int(job["attempt_count"]), content_sha256
            )
            indexer = self.indexer_factory(str(job["target"]))
            indexer.commit(
                prepared.chunks_path,
                journal_dir=workspace / "rollback",
                identity=identity,
                on_state=lambda state, path: self._on_atomic_state(
                    job_id, state, path
                ),
            )
        except Exception as exc:
            self._handle_commit_exception(
                job_id, job, workspace, prepared, indexer, exc
            )
            return

        self._finish_committed_success(
            job_id,
            chunk_total=prepared.chunk_count,
            warning_count=prepared.warning_count,
        )

    def execute_recovery(self, job_id: str) -> None:
        delay = 2.0
        while not self._stop_event.is_set():
            job = self.store.get_job_for_execution(job_id)
            if job is None or job["status"] not in {"running", "rolling_back"}:
                return
            attempt = job.get("attempt")
            if not isinstance(attempt, dict):
                return
            try:
                journal = self._journal_for_job(job)
            except _UntrustedRecoveryMaterial:
                return
            else:
                if journal is None:
                    return
                state = None
            indexer: _AtomicIndexer | None = None
            if state is None and _is_trusted_journal(job, journal):
                try:
                    identity = _identity_for_job(job)
                    indexer = self.indexer_factory(str(job["target"]))
                    indexer.recover(journal, expected_identity=identity)
                    state = indexer.inspect_journal_state(
                        journal, expected_identity=identity
                    )
                except UntrustedJournalError:
                    return
                except _UntrustedRecoveryMaterial:
                    return
                except Exception:
                    if indexer is not None:
                        try:
                            state = indexer.inspect_journal_state(
                                journal, expected_identity=identity
                            )
                        except UntrustedJournalError:
                            return
                        except Exception:
                            state = None

            if state == "committed":
                if not self._reconcile_committed(job_id, journal):
                    return
                self._finish_committed_success(
                    job_id,
                    chunk_total=int(job["chunk_total"]),
                    warning_count=int(job["warning_count"]),
                )
                return
            if state == "rolled_back":
                if not self._reconcile_rolled_back(job_id, journal):
                    return
                current = self.store.get_job_for_execution(job_id)
                if current is None or not self._cleanup_attempts(current):
                    return
                code, detail = safe_ingestion_error("index_failed")
                self.store.fail_job(job_id, code=code, detail=detail)
                return

            if state in {"prepared", "rolling_back"}:
                self._ensure_rolling_back(job_id, journal)
            if not self._sleep(delay):
                return
            delay = min(delay * 2.0, 60.0)

    def execute_interrupt(self, job_id: str) -> None:
        delay = 2.0
        while not self._stop_event.is_set():
            try:
                disposition = self._fail_interrupted(
                    RecoveryAction(job_id=job_id, action="fail_interrupted")
                )
                if disposition == "recovery":
                    self.execute_recovery(job_id)
                    return
                if disposition == "retry":
                    if not self._sleep(delay):
                        return
                    delay = min(delay * 2.0, 60.0)
                    continue
                return
            except Exception:
                if not self._sleep(delay):
                    return
                delay = min(delay * 2.0, 60.0)

    def recover_after_restart(self) -> None:
        actions = self.store.recovery_actions()
        for action in actions:
            try:
                if action.action in {"rollback", "finish_committed"}:
                    self._put("recovery", action.job_id, priority=0)
                elif action.action == "fail_interrupted":
                    self._put("interrupt", action.job_id, priority=0)
                elif action.action == "delete":
                    self._put("delete", action.job_id, priority=0)
                elif action.action == "enqueue":
                    self._put("job", action.job_id, priority=10)
            except Exception:
                continue

    def _handle_commit_exception(
        self,
        job_id: str,
        job: Mapping[str, object],
        workspace: Path,
        prepared: PreparedIngestion,
        indexer: _AtomicIndexer | None,
        exc: Exception,
    ) -> None:
        current = self.store.get_job_for_execution(job_id)
        if current is None:
            return
        paths = _expected_paths(current)
        if workspace != paths.workspace:
            return
        if isinstance(exc, RollbackPendingError) and not _lexical_path_equals(
            Path(exc.journal_path), paths.journal
        ):
            return
        try:
            journal = self._journal_for_job(current)
        except _UntrustedRecoveryMaterial:
            return

        if journal is None:
            if isinstance(exc, RollbackPendingError):
                self._ensure_rolling_back(job_id, paths.journal)
                return
            self._finish_precommit_failure(job_id, job, exc)
            return

        if indexer is None:
            self._finish_precommit_failure(job_id, job, exc)
            return
        try:
            state = indexer.inspect_journal_state(
                journal, expected_identity=_identity_for_job(current)
            )
        except UntrustedJournalError:
            return
        except Exception:
            if isinstance(exc, RollbackPendingError):
                self._ensure_rolling_back(job_id, journal)
            else:
                self._put("recovery", job_id, priority=0)
            return

        if state == "committed":
            if not self._reconcile_committed(job_id, journal):
                return
            self._finish_committed_success(
                job_id,
                chunk_total=prepared.chunk_count,
                warning_count=prepared.warning_count,
            )
            return
        if state == "rolled_back":
            if not self._reconcile_rolled_back(job_id, journal):
                return
            current = self.store.get_job_for_execution(job_id)
            if current is None or not self._cleanup_attempts(current):
                return
            code, detail = safe_ingestion_error("index_failed")
            self.store.fail_job(job_id, code=code, detail=detail)
            return

        self._ensure_rolling_back(job_id, journal)

    def _on_pipeline_event(
        self,
        job_id: str,
        job: Mapping[str, object],
        event: str,
        payload: Mapping[str, object],
    ) -> None:
        valid_indexes = {
            item["item_index"]
            for item in cast(list[dict[str, object]], job.get("items", []))
        }
        item_index = payload.get("item_index")
        if (
            isinstance(item_index, bool)
            or not isinstance(item_index, int)
            or item_index not in valid_indexes
        ):
            return
        if event == "item_started":
            stage = payload.get("stage")
            if stage not in {"parsing", "chunking"}:
                stage = "parsing"
            self.store.mark_item_running(job_id, item_index, cast(str, stage))
        elif event == "item_completed":
            chunk_count = payload.get("chunk_count")
            warning_count = payload.get("warning_count")
            if _is_nonnegative_int(chunk_count) and _is_nonnegative_int(warning_count):
                self.store.complete_item(job_id, item_index, chunk_count, warning_count)
        elif event == "item_failed":
            self.store.fail_item(job_id, item_index, "")

    def _on_atomic_state(self, job_id: str, state: str, raw_path: Path) -> None:
        job = self.store.get_job_for_execution(job_id)
        if job is None:
            raise _UntrustedRecoveryMaterial("任务不存在")
        paths = _expected_paths(job)
        if (
            not isinstance(raw_path, Path)
            or not raw_path.is_absolute()
            or any(part in {".", ".."} for part in raw_path.parts)
            or str(raw_path) != str(paths.journal)
        ):
            raise _UntrustedRecoveryMaterial("Atomic callback journal path 不可信")
        _validate_attempt_binding(job, paths, allow_workspace_none=False)
        _validate_recovery_location(paths, paths.rollback_dir)
        self.store.mark_commit_state(job_id, state, raw_path)

    def _finish_precommit_failure(
        self,
        job_id: str,
        job: Mapping[str, object],
        exc: Exception,
    ) -> None:
        if not self._cleanup_attempts(job):
            return
        code, detail = ingestion_exception_error(exc)
        self.store.fail_job(job_id, code=code, detail=detail)

    def _fail_active_items(
        self,
        job_id: str,
        job: Mapping[str, object],
        exc: Exception,
    ) -> None:
        item_index = getattr(exc, "item_index", None)
        if _is_positive_int(item_index):
            self.store.fail_item(job_id, item_index, "")
        current = self.store.get_job(job_id, include_events=False)
        if current is None:
            return
        for item in current["items"]:
            if item["status"] == "running":
                self.store.fail_item(job_id, int(item["item_index"]), "")

    def _ensure_rolling_back(self, job_id: str, journal: Path) -> bool:
        try:
            current = self.store.get_job_for_execution(job_id)
            if current is None or not isinstance(current.get("attempt"), dict):
                return False
            state = current["attempt"]["commit_state"]
            if state == "not_started":
                self.store.mark_commit_state(job_id, "prepared", journal)
                state = "prepared"
            if state == "prepared":
                code, detail = safe_ingestion_error("rollback_pending")
                self.store.mark_rolling_back(job_id, code=code, detail=detail)
            return state in {"prepared", "rolling_back"}
        except Exception:
            return False

    def _reconcile_committed(self, job_id: str, journal: Path) -> bool:
        try:
            current = self.store.get_job_for_execution(job_id)
            if current is None or not isinstance(current.get("attempt"), dict):
                return False
            state = current["attempt"]["commit_state"]
            if state == "not_started":
                self.store.mark_commit_state(job_id, "prepared", journal)
                state = "prepared"
            if state == "prepared":
                self.store.mark_commit_state(job_id, "committed", journal)
                state = "committed"
            return state == "committed"
        except Exception:
            return False

    def _reconcile_rolled_back(self, job_id: str, journal: Path) -> bool:
        try:
            current = self.store.get_job_for_execution(job_id)
            if current is None or not isinstance(current.get("attempt"), dict):
                return False
            state = current["attempt"]["commit_state"]
            if state == "not_started":
                self.store.mark_commit_state(job_id, "prepared", journal)
                state = "prepared"
            if state == "prepared":
                code, detail = safe_ingestion_error("index_failed")
                self.store.mark_rolling_back(job_id, code=code, detail=detail)
                state = "rolling_back"
            if state == "rolling_back":
                self.store.mark_commit_state(job_id, "rolled_back", journal)
                state = "rolled_back"
            return state == "rolled_back"
        except Exception:
            return False

    def _cleanup_attempts(self, job: Mapping[str, object]) -> bool:
        try:
            job_id = job.get("job_id")
            current = (
                self.store.get_job_for_execution(job_id)
                if isinstance(job_id, str)
                else None
            )
            checked = current or job
            paths = _expected_paths(checked)
            _validate_attempt_binding(checked, paths, allow_workspace_none=True)
            _validate_directory_chain(paths, include_rollback=True)
            attempt = checked.get("attempt")
            if (
                isinstance(attempt, dict)
                and attempt.get("workspace_path") is None
                and attempt.get("commit_state") == "not_started"
                and attempt.get("status") == "queued"
                and paths.workspace.is_dir()
                and any(paths.workspace.iterdir())
            ):
                raise _UntrustedRecoveryMaterial("未持久化 workspace 非空")
            clear_attempt_workspaces(paths.job_dir)
        except Exception:
            return False
        return True

    def _finish_committed_success(
        self, job_id: str, *, chunk_total: int, warning_count: int
    ) -> bool:
        job = self.store.get_job_for_execution(job_id)
        if job is None:
            return False
        backup = self._stash_committed_recovery(job)
        if backup is None:
            return False
        try:
            self.store.succeed_job(job_id, chunk_total, warning_count)
        except Exception:
            self._restore_recovery_material(job, backup)
            return False
        try:
            _validate_recovery_location(_expected_paths(job), backup)
            delete_job_workspace(backup)
        except Exception:
            pass
        return True

    def _stash_committed_recovery(
        self, job: Mapping[str, object]
    ) -> Path | None:
        try:
            paths = _expected_paths(job)
            journal = self._journal_for_job(job)
            if journal is None:
                return None
            backup = paths.backup
            _validate_recovery_location(paths, backup)
            if backup.exists() or backup.is_symlink():
                return None
            journal.parent.replace(backup)
            _fsync_directory(journal.parent.parent)
            _fsync_directory(_trusted_job_dir(job))
            if not self._cleanup_attempts(job):
                self._restore_recovery_material(job, backup)
                return None
            return backup
        except Exception:
            if "backup" in locals():
                self._restore_recovery_material(job, backup)
            return None

    def _journal_for_job(self, job: Mapping[str, object]) -> Path | None:
        paths = _expected_paths(job)
        attempt = job.get("attempt")
        if (
            isinstance(attempt, dict)
            and attempt.get("workspace_path") is None
            and attempt.get("journal_path") is None
            and attempt.get("commit_state") == "not_started"
        ):
            _validate_attempt_binding(job, paths, allow_workspace_none=True)
            _validate_directory_chain(paths, include_rollback=True)
            if paths.workspace.is_dir():
                if any(paths.workspace.iterdir()):
                    raise _UntrustedRecoveryMaterial("未开始 attempt 存在异常恢复材料")
                return None
            if paths.workspace.exists() or paths.workspace.is_symlink():
                raise _UntrustedRecoveryMaterial("未开始 attempt 工作区不可信")
            return None
        _validate_attempt_binding(job, paths, allow_workspace_none=False)
        _validate_recovery_location(paths, paths.rollback_dir)
        if paths.journal.is_file():
            return paths.journal
        _validate_recovery_location(paths, paths.backup)
        if paths.backup.is_dir() and self._restore_recovery_material(job, paths.backup):
            _validate_recovery_location(paths, paths.rollback_dir)
            return paths.journal if paths.journal.is_file() else None
        return None

    def _restore_recovery_material(
        self, job: Mapping[str, object], backup: Path
    ) -> bool:
        try:
            paths = _expected_paths(job)
            _validate_attempt_binding(job, paths, allow_workspace_none=False)
            if backup != paths.backup:
                raise _UntrustedRecoveryMaterial("恢复备份路径不可信")
            _validate_recovery_location(paths, backup)
            _validate_recovery_location(paths, paths.rollback_dir)
            if paths.rollback_dir.is_dir():
                return paths.journal.is_file()
            paths.rollback_dir.parent.mkdir(parents=True, exist_ok=True)
            backup.replace(paths.rollback_dir)
            _fsync_directory(paths.rollback_dir.parent)
            _fsync_directory(paths.job_dir)
            _validate_recovery_location(paths, paths.rollback_dir)
            return paths.journal.is_file()
        except Exception:
            return False

    def _fail_interrupted(
        self, action: RecoveryAction
    ) -> Literal["done", "recovery", "quarantined", "retry"]:
        job = self.store.get_job_for_execution(action.job_id)
        if job is None:
            return "done"
        try:
            journal = self._journal_for_job(job)
        except _UntrustedRecoveryMaterial:
            return "quarantined"
        if journal is not None:
            try:
                indexer = self.indexer_factory(str(job["target"]))
                state = indexer.inspect_journal_state(
                    journal, expected_identity=_identity_for_job(job)
                )
            except UntrustedJournalError:
                return "quarantined"
            except _UntrustedRecoveryMaterial:
                return "quarantined"
            except Exception:
                return "recovery"
            if state == "committed":
                self._reconcile_committed(action.job_id, journal)
            elif state == "rolled_back":
                if not self._reconcile_rolled_back(action.job_id, journal):
                    return "recovery"
                current = self.store.get_job_for_execution(action.job_id)
                if current is None or not self._cleanup_attempts(current):
                    return "recovery"
                code, detail = safe_ingestion_error("index_failed")
                self.store.fail_job(action.job_id, code=code, detail=detail)
                return "done"
            else:
                self._ensure_rolling_back(action.job_id, journal)
            return "recovery"
        if not self._cleanup_attempts(job):
            current = self.store.get_job_for_execution(action.job_id)
            if current is None:
                return "done"
            try:
                self._journal_for_job(current)
            except _UntrustedRecoveryMaterial:
                return "quarantined"
            return "retry"
        attempt = job.get("attempt")
        error_code = (
            "index_failed"
            if isinstance(attempt, dict) and attempt.get("commit_state") == "rolled_back"
            else "service_restarted"
        )
        code, detail = safe_ingestion_error(error_code)
        self.store.fail_job(action.job_id, code=code, detail=detail)
        return "done"

    def _execute_delete(self, job_id: str) -> None:
        job = self.store.get_job_for_execution(job_id)
        if job is None:
            return
        try:
            job_dir = _trusted_job_dir(job)
            delete_job_workspace(job_dir)
        except Exception:
            return
        self.store.finish_delete(job_id)

    def _sleep(self, delay: float) -> bool:
        if self._stop_event.is_set():
            return False
        if self._sleep_fn is None:
            return not self._stop_event.wait(delay)
        self._sleep_fn(delay)
        return not self._stop_event.is_set()

    def _put(self, kind: QueueKind, job_id: str, *, priority: int) -> None:
        self._queue.put(_QueueItem(priority, next(self._sequence), kind, job_id))

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item.kind == "stop":
                    return
                try:
                    if item.kind == "job":
                        self.execute_job(item.job_id)
                    elif item.kind == "recovery":
                        self.execute_recovery(item.job_id)
                    elif item.kind == "interrupt":
                        self.execute_interrupt(item.job_id)
                    elif item.kind == "delete":
                        self._execute_delete(item.job_id)
                except Exception:
                    pass
            finally:
                self._queue.task_done()


def _trusted_job_dir(job: Mapping[str, object]) -> Path:
    source_path = job.get("source_path")
    jobs_root = job.get("jobs_root")
    job_id = job.get("job_id")
    if (
        not isinstance(source_path, Path)
        or not isinstance(jobs_root, Path)
        or not isinstance(job_id, str)
        or not source_path.is_absolute()
        or source_path.parent.name != "source"
    ):
        raise _UntrustedRecoveryMaterial("入库任务工作区无效")
    job_dir = source_path.parent.parent
    if job_dir.name != job_id or str(job_dir.parent) != str(jobs_root):
        raise _UntrustedRecoveryMaterial("任务目录未绑定 jobs_root/job_id")
    if jobs_root.exists() and (
        jobs_root.is_symlink() or jobs_root.resolve(strict=True) != jobs_root
    ):
        raise _UntrustedRecoveryMaterial("jobs_root 不可信")
    if source_path != source_path.resolve(strict=False):
        raise _UntrustedRecoveryMaterial("入库源路径不可信")
    if job_dir.exists() and (
        job_dir.is_symlink() or job_dir.resolve(strict=True) != job_dir
    ):
        raise _UntrustedRecoveryMaterial("入库任务目录不可信")
    return job_dir


def _expected_paths(job: Mapping[str, object]) -> _ExpectedPaths:
    job_dir = _trusted_job_dir(job)
    attempt = job.get("attempt")
    attempt_count = job.get("attempt_count")
    if (
        not isinstance(attempt, dict)
        or not _is_positive_int(attempt_count)
        or attempt.get("attempt_no") != attempt_count
    ):
        raise _UntrustedRecoveryMaterial("当前 attempt 绑定无效")
    attempt_no = cast(int, attempt_count)
    attempts_dir = job_dir / "attempts"
    workspace = attempts_dir / str(attempt_no)
    rollback_dir = workspace / "rollback"
    return _ExpectedPaths(
        job_dir=job_dir,
        attempts_dir=attempts_dir,
        attempt_no=attempt_no,
        workspace=workspace,
        rollback_dir=rollback_dir,
        journal=rollback_dir / "journal.json",
        backup=job_dir / f".recovery-attempt-{attempt_no}",
    )


def _validate_attempt_binding(
    job: Mapping[str, object],
    paths: _ExpectedPaths,
    *,
    allow_workspace_none: bool,
) -> None:
    attempt = job.get("attempt")
    if not isinstance(attempt, dict):
        raise _UntrustedRecoveryMaterial("当前 attempt 缺失")
    workspace = attempt.get("workspace_path")
    if workspace is None and allow_workspace_none:
        pass
    elif not _lexical_path_equals(workspace, paths.workspace):
        raise _UntrustedRecoveryMaterial("attempt workspace 绑定无效")
    stored_journal = attempt.get("journal_path")
    if stored_journal is not None and not _lexical_path_equals(
        stored_journal, paths.journal
    ):
        raise _UntrustedRecoveryMaterial("commit journal 绑定无效")


def _lexical_path_equals(value: object, expected: Path) -> bool:
    return (
        isinstance(value, Path)
        and value.is_absolute()
        and str(value) == str(expected)
    )


def _validate_directory_chain(
    paths: _ExpectedPaths, *, include_rollback: bool
) -> None:
    components = [paths.attempts_dir, paths.workspace]
    if include_rollback:
        components.extend((paths.rollback_dir, paths.journal))
    for index, component in enumerate(components):
        if not component.exists() and not component.is_symlink():
            continue
        metadata = component.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise _UntrustedRecoveryMaterial("恢复路径包含符号链接")
        is_final_file = include_rollback and index == len(components) - 1
        if is_final_file:
            if not stat.S_ISREG(metadata.st_mode):
                raise _UntrustedRecoveryMaterial("journal 不是常规文件")
        elif not stat.S_ISDIR(metadata.st_mode):
            raise _UntrustedRecoveryMaterial("恢复路径组件不是目录")
        resolved = component.resolve(strict=True)
        if not resolved.is_relative_to(paths.job_dir):
            raise _UntrustedRecoveryMaterial("恢复路径越过任务目录")


def _validate_recovery_location(paths: _ExpectedPaths, location: Path) -> None:
    if location == paths.rollback_dir:
        _validate_directory_chain(paths, include_rollback=True)
        return
    if location != paths.backup:
        raise _UntrustedRecoveryMaterial("恢复材料位置无效")
    if not location.exists() and not location.is_symlink():
        return
    metadata = location.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise _UntrustedRecoveryMaterial("恢复备份不是可信目录")
    if not location.resolve(strict=True).is_relative_to(paths.job_dir):
        raise _UntrustedRecoveryMaterial("恢复备份越过任务目录")
    for entry in location.iterdir():
        entry_metadata = entry.lstat()
        if stat.S_ISLNK(entry_metadata.st_mode) or not stat.S_ISREG(
            entry_metadata.st_mode
        ):
            raise _UntrustedRecoveryMaterial("恢复备份包含不可信条目")
        if not entry.resolve(strict=True).is_relative_to(location):
            raise _UntrustedRecoveryMaterial("恢复备份条目越界")


def _is_trusted_journal(job: Mapping[str, object], journal: Path) -> bool:
    try:
        return _journal_path_without_restore(job) == journal
    except _UntrustedRecoveryMaterial:
        return False


def _identity_for_job(job: Mapping[str, object]) -> AtomicJournalIdentity:
    attempt = job.get("attempt")
    job_id = job.get("job_id")
    if not isinstance(attempt, dict) or not isinstance(job_id, str):
        raise _UntrustedRecoveryMaterial("journal owner identity 缺失")
    digest = attempt.get("content_sha256")
    attempt_no = attempt.get("attempt_no")
    if (
        not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or not _is_positive_int(attempt_no)
    ):
        raise _UntrustedRecoveryMaterial("journal owner identity 无效")
    return AtomicJournalIdentity(job_id, cast(int, attempt_no), digest)


def _journal_path_without_restore(job: Mapping[str, object]) -> Path | None:
    paths = _expected_paths(job)
    _validate_attempt_binding(job, paths, allow_workspace_none=False)
    _validate_recovery_location(paths, paths.rollback_dir)
    return paths.journal if paths.journal.is_file() else None


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
