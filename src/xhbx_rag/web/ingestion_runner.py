from __future__ import annotations

import itertools
import os
import queue
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, cast

from xhbx_rag.atomic_indexer import AtomicIndexResult, RollbackPendingError
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
        on_state: Callable[[str, Path], None] | None = None,
    ) -> AtomicIndexResult: ...

    def recover(self, journal_path: Path) -> None: ...

    def inspect_journal_state(self, journal_path: Path) -> str: ...


QueueKind = Literal["job", "recovery", "delete", "stop"]


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
            job_dir = _job_dir(job)
            attempt_no = int(job["attempt_count"])
            workspace = job_dir / "attempts" / str(attempt_no)
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
            indexer = self.indexer_factory(str(job["target"]))
            indexer.commit(
                prepared.chunks_path,
                journal_dir=workspace / "rollback",
                on_state=lambda state, path: self.store.mark_commit_state(
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
        retry = 0
        while not self._stop_event.is_set():
            job = self.store.get_job_for_execution(job_id)
            if job is None or job["status"] not in {"running", "rolling_back"}:
                return
            attempt = job.get("attempt")
            if not isinstance(attempt, dict):
                return
            journal = self._journal_for_job(job)
            if journal is None:
                return
            indexer: _AtomicIndexer | None = None
            state: str | None = None
            try:
                indexer = self.indexer_factory(str(job["target"]))
                indexer.recover(journal)
                state = indexer.inspect_journal_state(journal)
            except Exception:
                if indexer is not None:
                    try:
                        state = indexer.inspect_journal_state(journal)
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

            self._ensure_rolling_back(job_id, journal)
            delay = min(2.0 * (2**retry), 60.0)
            retry += 1
            if not self._sleep(delay):
                return

    def recover_after_restart(self) -> None:
        actions = self.store.recovery_actions()
        discovered_recovery: list[str] = []
        for action in actions:
            if action.action == "fail_interrupted":
                if self._fail_interrupted(action):
                    discovered_recovery.append(action.job_id)

        queued_recovery: set[str] = set()
        for action in actions:
            if action.action in {"rollback", "finish_committed"}:
                self._put("recovery", action.job_id, priority=0)
                queued_recovery.add(action.job_id)
            elif action.action == "delete":
                self._put("delete", action.job_id, priority=0)
        for job_id in discovered_recovery:
            if job_id not in queued_recovery:
                self._put("recovery", job_id, priority=0)
        for action in actions:
            if action.action == "enqueue":
                self._put("job", action.job_id, priority=10)

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
        attempt = current.get("attempt") if current is not None else None
        stored_journal = attempt.get("journal_path") if isinstance(attempt, dict) else None
        journal: Path | None
        if isinstance(exc, RollbackPendingError):
            journal = Path(exc.journal_path).resolve()
        elif isinstance(stored_journal, Path):
            journal = stored_journal.resolve()
        else:
            candidate = (workspace / "rollback" / "journal.json").resolve()
            journal = candidate if candidate.exists() else None

        if journal is None:
            self._finish_precommit_failure(job_id, job, exc)
            return

        if indexer is None:
            self._finish_precommit_failure(job_id, job, exc)
            return
        try:
            state = indexer.inspect_journal_state(journal)
        except Exception:
            self._ensure_rolling_back(job_id, journal)
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
            clear_attempt_workspaces(_job_dir(job))
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
            delete_job_workspace(backup)
        except Exception:
            pass
        return True

    def _stash_committed_recovery(
        self, job: Mapping[str, object]
    ) -> Path | None:
        journal = self._journal_for_job(job)
        if journal is None:
            return None
        backup = _recovery_backup_path(job)
        try:
            if backup.exists() or backup.is_symlink():
                return None
            journal.parent.replace(backup)
            _fsync_directory(journal.parent.parent)
            _fsync_directory(_job_dir(job))
            if not self._cleanup_attempts(job):
                self._restore_recovery_material(job, backup)
                return None
            return backup
        except Exception:
            self._restore_recovery_material(job, backup)
            return None

    def _journal_for_job(self, job: Mapping[str, object]) -> Path | None:
        attempt = job.get("attempt")
        if not isinstance(attempt, dict):
            return None
        stored = attempt.get("journal_path")
        workspace = attempt.get("workspace_path")
        if isinstance(stored, Path):
            journal = stored.resolve()
        elif isinstance(workspace, Path):
            journal = (workspace / "rollback" / "journal.json").resolve()
        else:
            return None
        if journal.is_file():
            return journal
        backup = _recovery_backup_path(job)
        if backup.is_dir() and self._restore_recovery_material(job, backup):
            return journal if journal.is_file() else None
        return None

    def _restore_recovery_material(
        self, job: Mapping[str, object], backup: Path
    ) -> bool:
        attempt = job.get("attempt")
        if not isinstance(attempt, dict):
            return False
        stored = attempt.get("journal_path")
        workspace = attempt.get("workspace_path")
        if isinstance(stored, Path):
            rollback_dir = stored.resolve().parent
        elif isinstance(workspace, Path):
            rollback_dir = (workspace / "rollback").resolve()
        else:
            return False
        try:
            if rollback_dir.is_dir():
                return (rollback_dir / "journal.json").is_file()
            rollback_dir.parent.mkdir(parents=True, exist_ok=True)
            backup.replace(rollback_dir)
            _fsync_directory(rollback_dir.parent)
            _fsync_directory(_job_dir(job))
            return (rollback_dir / "journal.json").is_file()
        except Exception:
            return False

    def _fail_interrupted(self, action: RecoveryAction) -> bool:
        job = self.store.get_job_for_execution(action.job_id)
        if job is None:
            return False
        journal = self._journal_for_job(job)
        if journal is not None:
            try:
                indexer = self.indexer_factory(str(job["target"]))
                state = indexer.inspect_journal_state(journal)
            except Exception:
                self._ensure_rolling_back(action.job_id, journal)
                return True
            if state == "committed":
                self._reconcile_committed(action.job_id, journal)
            elif state == "rolled_back":
                if not self._reconcile_rolled_back(action.job_id, journal):
                    return True
                current = self.store.get_job_for_execution(action.job_id)
                if current is None or not self._cleanup_attempts(current):
                    return False
                code, detail = safe_ingestion_error("index_failed")
                self.store.fail_job(action.job_id, code=code, detail=detail)
                return False
            else:
                self._ensure_rolling_back(action.job_id, journal)
            return True
        if not self._cleanup_attempts(job):
            return False
        attempt = job.get("attempt")
        error_code = (
            "index_failed"
            if isinstance(attempt, dict) and attempt.get("commit_state") == "rolled_back"
            else "service_restarted"
        )
        code, detail = safe_ingestion_error(error_code)
        self.store.fail_job(action.job_id, code=code, detail=detail)
        return False

    def _execute_delete(self, job_id: str) -> None:
        job = self.store.get_job_for_execution(job_id)
        if job is None:
            return
        try:
            delete_job_workspace(_job_dir(job))
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
                    elif item.kind == "delete":
                        self._execute_delete(item.job_id)
                except Exception:
                    pass
            finally:
                self._queue.task_done()


def _job_dir(job: Mapping[str, object]) -> Path:
    source_path = job.get("source_path")
    if not isinstance(source_path, Path) or source_path.parent.name != "source":
        raise ValueError("入库任务工作区无效")
    return source_path.parent.parent


def _recovery_backup_path(job: Mapping[str, object]) -> Path:
    attempt = job.get("attempt")
    if not isinstance(attempt, dict) or not _is_positive_int(attempt.get("attempt_no")):
        raise ValueError("入库 attempt 无效")
    return _job_dir(job) / f".recovery-attempt-{attempt['attempt_no']}"


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
