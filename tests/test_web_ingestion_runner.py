from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

import pytest

import xhbx_rag.web.ingestion_runner as runner_module
from xhbx_rag.atomic_indexer import AtomicIndexError, AtomicIndexResult, RollbackPendingError
from xhbx_rag.web.ingestion_pipeline import IngestionPipelineError, PreparedIngestion
from xhbx_rag.web.ingestion_runner import IngestionRunner
from xhbx_rag.web.ingestion_store import IngestionStore
from xhbx_rag.web.ingestion_uploads import PreflightItem, PreflightResult
from xhbx_rag.web.safe_errors import ingestion_exception_error, safe_ingestion_error


def queued_store(
    tmp_path: Path,
    *,
    name: str = "job",
    item_count: int = 1,
) -> tuple[IngestionStore, str, Path]:
    store = IngestionStore(tmp_path / "ingestion.sqlite3")
    job_dir = tmp_path / "jobs" / name
    source = job_dir / "source" / "courses.zip"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    items = tuple(
        PreflightItem(index, f"course-{index}", f"课程{index}", (f"{index}.txt",), 1)
        for index in range(1, item_count + 1)
    )
    job = store.create_draft(
        preflight=PreflightResult("courses.zip", "zip", "course", items),
        source_path=source,
    )
    assert store.start_job(job["job_id"]) == "ok"
    return store, job["job_id"], job_dir


def attempt_dir(store: IngestionStore, job_id: str) -> Path:
    job = store.get_job_for_execution(job_id)
    assert job is not None
    workspace = job["attempt"]["workspace_path"]
    assert isinstance(workspace, Path)
    return workspace


class FakePipeline:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        chunk_count: int = 1,
        warning_count: int = 0,
        events: list[tuple[str, dict[str, object]]] | None = None,
        log: list[str] | None = None,
    ) -> None:
        self.error = error
        self.chunk_count = chunk_count
        self.warning_count = warning_count
        self.events = events or []
        self.log = log
        self.calls: list[str] = []

    def prepare(
        self,
        job: dict[str, object],
        workspace: Path,
        *,
        on_event: Callable[[str, dict[str, object]], None] | None = None,
    ) -> PreparedIngestion:
        job_id = str(job["job_id"])
        self.calls.append(job_id)
        if self.log is not None:
            self.log.append(f"prepare:{job_id}")
        workspace.mkdir(parents=True, exist_ok=True)
        for event, payload in self.events:
            if on_event is not None:
                on_event(event, payload)
        if self.error is not None:
            raise self.error
        chunks_path = workspace / "staging" / "chunks.jsonl"
        chunks_path.parent.mkdir(parents=True, exist_ok=True)
        chunks_path.write_text("{}\n", encoding="utf-8")
        return PreparedIngestion(
            chunks_path=chunks_path,
            chunk_count=self.chunk_count,
            warning_count=self.warning_count,
            warnings=(),
        )


class FakeAtomicIndexer:
    def __init__(
        self,
        *,
        mode: str = "success",
        recover_failures: int = 0,
        log: list[str] | None = None,
    ) -> None:
        self.mode = mode
        self.recover_failures = recover_failures
        self.log = log
        self.commit_calls = 0
        self.recover_calls = 0
        self.states: dict[Path, str] = {}

    def commit(
        self,
        chunks_path: Path,
        *,
        journal_dir: Path,
        on_state: Callable[[str, Path], None] | None = None,
    ) -> AtomicIndexResult:
        del chunks_path
        self.commit_calls += 1
        if self.mode == "embedding_failure":
            raise AtomicIndexError("向量生成失败")
        if self.mode == "precommit_failure":
            raise AtomicIndexError("内部 /private/path token=secret")

        journal_dir.mkdir(parents=True, exist_ok=True)
        journal = (journal_dir / "journal.json").resolve()
        journal.write_text("fake", encoding="utf-8")
        (journal_dir / "snapshot.jsonl").write_text("fake", encoding="utf-8")
        if self.mode == "rollback_pending_without_callback":
            self.states[journal] = "rolling_back"
            raise RollbackPendingError(journal, "Milvus /private/path token=secret")

        self.states[journal] = "prepared"
        if on_state is not None:
            on_state("prepared", journal)
        if self.mode == "rolled_back":
            self.states[journal] = "rolled_back"
            raise AtomicIndexError("知识库写入失败，已完成回滚")
        if self.mode == "corrupt":
            raise AtomicIndexError("知识库写入失败")

        self.states[journal] = "committed"
        if self.mode == "committed_without_callback":
            raise AtomicIndexError("知识库已提交，但状态同步失败")
        if on_state is not None:
            on_state("committed", journal)
        return AtomicIndexResult(indexed=1, vector_dim=2)

    def inspect_journal_state(self, journal_path: Path) -> str:
        if self.mode == "corrupt":
            raise AtomicIndexError("commit journal 自校验失败")
        return self.states[journal_path.resolve()]

    def recover(self, journal_path: Path) -> None:
        self.recover_calls += 1
        if self.log is not None:
            self.log.append(f"recover:{journal_path.parent.parent.parent.name}")
        if self.mode == "corrupt" or self.recover_failures > 0:
            if self.recover_failures > 0:
                self.recover_failures -= 1
            raise AtomicIndexError("知识库恢复失败 /private/path token=secret")
        path = journal_path.resolve()
        if self.states[path] in {"prepared", "rolling_back"}:
            self.states[path] = "rolled_back"


def _runner(
    store: IngestionStore,
    pipeline: FakePipeline,
    indexer: FakeAtomicIndexer,
    **kwargs: object,
) -> IngestionRunner:
    return IngestionRunner(
        store=store,
        pipeline=pipeline,
        indexer_factory=lambda target: indexer,
        **kwargs,
    )


def _prepare_recovery(
    store: IngestionStore,
    job_id: str,
    job_dir: Path,
    indexer: FakeAtomicIndexer,
    *,
    sqlite_state: str = "prepared",
    journal_state: str = "prepared",
) -> Path:
    assert store.claim_job(job_id)
    workspace = job_dir / "attempts" / "1"
    workspace.mkdir(parents=True)
    store.begin_attempt(job_id, workspace)
    journal = (workspace / "rollback" / "journal.json").resolve()
    journal.parent.mkdir(parents=True)
    journal.write_text("fake", encoding="utf-8")
    (journal.parent / "snapshot.jsonl").write_text("fake", encoding="utf-8")
    indexer.states[journal] = journal_state
    store.mark_commit_state(job_id, "prepared", journal)
    if sqlite_state == "rolling_back":
        store.mark_rolling_back(job_id, code="rollback_pending", detail="raw secret")
    return journal


def test_runner_success_maps_events_commits_and_cleans_attempts(tmp_path: Path) -> None:
    store, job_id, job_dir = queued_store(tmp_path, item_count=1)
    pipeline = FakePipeline(
        chunk_count=3,
        warning_count=2,
        events=[
            ("item_started", {"item_index": 1, "stage": "parsing"}),
            (
                "course_file",
                {
                    "item_index": 1,
                    "relative_path": "/private/course.txt",
                    "body": "模型正文 secret-token",
                },
            ),
            ("item_completed", {"item_index": 1, "chunk_count": 3, "warning_count": 2}),
        ],
    )
    indexer = FakeAtomicIndexer()

    _runner(store, pipeline, indexer).execute_job(job_id)

    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "succeeded"
    assert job["chunk_total"] == 3
    assert job["warning_count"] == 2
    assert job["items"][0]["status"] == "succeeded"
    assert indexer.commit_calls == 1
    assert list((job_dir / "attempts").iterdir()) == []
    assert (job_dir / "source" / "courses.zip").read_bytes() == b"source"
    assert "/private/course.txt" not in repr(job)
    assert "模型正文" not in repr(job)


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (IngestionPipelineError("parse_failed", "raw /private/path token=secret", 1), "parse_failed"),
        (IngestionPipelineError("evil_code", "raw /private/path token=secret", 1), "storage_unavailable"),
    ],
)
def test_runner_pipeline_failure_safely_fails_and_cleans(
    tmp_path: Path, error: Exception, expected_code: str
) -> None:
    store, job_id, job_dir = queued_store(tmp_path)
    pipeline = FakePipeline(
        error=error,
        events=[("item_started", {"item_index": 1, "stage": "parsing"})],
    )
    indexer = FakeAtomicIndexer()

    _runner(store, pipeline, indexer).execute_job(job_id)

    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert job["error_code"] == expected_code
    assert indexer.commit_calls == 0
    assert list((job_dir / "attempts").iterdir()) == []
    assert "private" not in repr(job)
    assert "secret" not in repr(job)


def test_runner_embedding_failure_is_fixed_and_precommit_clean(tmp_path: Path) -> None:
    store, job_id, job_dir = queued_store(tmp_path)
    indexer = FakeAtomicIndexer(mode="embedding_failure")

    _runner(store, FakePipeline(), indexer).execute_job(job_id)

    job = store.get_job(job_id)
    assert job is not None
    assert (job["status"], job["error_code"], job["error_detail"]) == (
        "failed",
        "embedding_failed",
        "向量生成失败",
    )
    assert list((job_dir / "attempts").iterdir()) == []


def test_runner_reconciles_physically_rolled_back_commit_before_failure(tmp_path: Path) -> None:
    store, job_id, job_dir = queued_store(tmp_path)

    _runner(store, FakePipeline(), FakeAtomicIndexer(mode="rolled_back")).execute_job(job_id)

    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert job["attempt"]["commit_state"] == "rolled_back"
    assert list((job_dir / "attempts").iterdir()) == []


def test_commit_exception_reuses_the_same_indexer_for_journal_inspection(
    tmp_path: Path,
) -> None:
    store, job_id, _ = queued_store(tmp_path)
    indexer = FakeAtomicIndexer(mode="rolled_back")
    factory_calls: list[str] = []
    runner = IngestionRunner(
        store=store,
        pipeline=FakePipeline(),
        indexer_factory=lambda target: factory_calls.append(target) or indexer,
    )

    runner.execute_job(job_id)

    assert factory_calls == ["course"]


def test_runner_treats_verified_committed_journal_as_success(tmp_path: Path) -> None:
    store, job_id, job_dir = queued_store(tmp_path)

    _runner(
        store,
        FakePipeline(chunk_count=4, warning_count=1),
        FakeAtomicIndexer(mode="committed_without_callback"),
    ).execute_job(job_id)

    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "succeeded"
    assert job["attempt"]["commit_state"] == "committed"
    assert job["chunk_total"] == 4
    assert list((job_dir / "attempts").iterdir()) == []


def test_rollback_pending_without_prepared_callback_is_reconciled_and_preserved(
    tmp_path: Path,
) -> None:
    store, job_id, job_dir = queued_store(tmp_path)

    _runner(
        store,
        FakePipeline(),
        FakeAtomicIndexer(mode="rollback_pending_without_callback"),
    ).execute_job(job_id)

    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "rolling_back"
    assert job["attempt"]["commit_state"] == "rolling_back"
    assert job["error_code"] == "rollback_pending"
    assert any((job_dir / "attempts").iterdir())


def test_corrupt_journal_stays_rolling_back_and_keeps_all_materials(tmp_path: Path) -> None:
    store, job_id, job_dir = queued_store(tmp_path)

    _runner(store, FakePipeline(), FakeAtomicIndexer(mode="corrupt")).execute_job(job_id)

    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "rolling_back"
    assert job["attempt"]["commit_state"] == "rolling_back"
    assert (job_dir / "attempts" / "1" / "rollback" / "journal.json").is_file()
    assert (job_dir / "attempts" / "1" / "staging" / "chunks.jsonl").is_file()


def test_execute_recovery_retries_2_4_then_rolls_back_and_cleans(tmp_path: Path) -> None:
    store, job_id, job_dir = queued_store(tmp_path)
    indexer = FakeAtomicIndexer(recover_failures=2)
    _prepare_recovery(store, job_id, job_dir, indexer)
    sleeps: list[float] = []

    _runner(store, FakePipeline(), indexer, sleep_fn=sleeps.append).execute_recovery(job_id)

    job = store.get_job(job_id)
    assert job is not None
    assert sleeps == [2.0, 4.0]
    assert indexer.recover_calls == 3
    assert job["status"] == "failed"
    assert job["attempt"]["commit_state"] == "rolled_back"
    assert list((job_dir / "attempts").iterdir()) == []


def test_execute_recovery_retries_when_indexer_factory_is_temporarily_unavailable(
    tmp_path: Path,
) -> None:
    store, job_id, job_dir = queued_store(tmp_path)
    indexer = FakeAtomicIndexer()
    _prepare_recovery(store, job_id, job_dir, indexer)
    sleeps: list[float] = []
    factory_calls = 0

    def flaky_factory(target: str) -> FakeAtomicIndexer:
        nonlocal factory_calls
        assert target == "course"
        factory_calls += 1
        if factory_calls < 3:
            raise RuntimeError("Milvus /private/path token=secret")
        return indexer

    runner = IngestionRunner(
        store=store,
        pipeline=FakePipeline(),
        indexer_factory=flaky_factory,
        sleep_fn=sleeps.append,
    )

    runner.execute_recovery(job_id)

    assert sleeps == [2.0, 4.0]
    assert factory_calls == 3
    assert store.get_job(job_id)["status"] == "failed"


def test_execute_recovery_finishes_verified_committed_without_rollback(tmp_path: Path) -> None:
    store, job_id, job_dir = queued_store(tmp_path)
    indexer = FakeAtomicIndexer()
    _prepare_recovery(store, job_id, job_dir, indexer, journal_state="committed")

    _runner(store, FakePipeline(), indexer).execute_recovery(job_id)

    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "succeeded"
    assert job["attempt"]["commit_state"] == "committed"
    assert list((job_dir / "attempts").iterdir()) == []


def test_execute_recovery_corruption_keeps_workspace_until_stop(tmp_path: Path) -> None:
    store, job_id, job_dir = queued_store(tmp_path)
    indexer = FakeAtomicIndexer(mode="corrupt")
    _prepare_recovery(store, job_id, job_dir, indexer)
    sleeps: list[float] = []
    runner: IngestionRunner

    def stop_after_first(delay: float) -> None:
        sleeps.append(delay)
        runner.stop()

    runner = _runner(store, FakePipeline(), indexer, sleep_fn=stop_after_first)
    runner.execute_recovery(job_id)

    job = store.get_job(job_id)
    assert job is not None
    assert sleeps == [2.0]
    assert job["status"] == "rolling_back"
    assert job["error_code"] == "rollback_pending"
    assert any((job_dir / "attempts").iterdir())


def test_recover_after_restart_immediately_fails_uncommitted_interruption(
    tmp_path: Path,
) -> None:
    store, job_id, job_dir = queued_store(tmp_path)
    assert store.claim_job(job_id)

    _runner(store, FakePipeline(), FakeAtomicIndexer()).recover_after_restart()

    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert job["error_code"] == "service_restarted"
    assert (job_dir / "source" / "courses.zip").is_file()
    assert list((job_dir / "attempts").iterdir()) == []


def test_restart_discovers_durable_journal_when_sqlite_is_still_not_started(
    tmp_path: Path,
) -> None:
    store, job_id, job_dir = queued_store(tmp_path)
    assert store.claim_job(job_id)
    workspace = job_dir / "attempts" / "1"
    workspace.mkdir(parents=True)
    store.begin_attempt(job_id, workspace)
    journal = (workspace / "rollback" / "journal.json").resolve()
    journal.parent.mkdir(parents=True)
    journal.write_text("fake", encoding="utf-8")
    (journal.parent / "snapshot.jsonl").write_text("fake", encoding="utf-8")
    indexer = FakeAtomicIndexer()
    indexer.states[journal] = "rolling_back"
    runner = _runner(store, FakePipeline(), indexer)

    runner.recover_after_restart()

    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "rolling_back"
    assert job["attempt"]["commit_state"] == "rolling_back"
    assert journal.is_file()
    assert (journal.parent / "snapshot.jsonl").is_file()

    runner.execute_recovery(job_id)
    assert store.get_job(job_id)["status"] == "failed"


def test_recovery_items_run_before_queued_jobs(tmp_path: Path) -> None:
    store, recovery_id, recovery_dir = queued_store(tmp_path, name="recovery")
    store2, queued_id, _ = queued_store(tmp_path, name="queued")
    assert store2.db_path == store.db_path
    log: list[str] = []
    indexer = FakeAtomicIndexer(log=log)
    _prepare_recovery(store, recovery_id, recovery_dir, indexer)
    pipeline = FakePipeline(log=log)
    runner = _runner(store, pipeline, indexer)

    runner.recover_after_restart()
    runner.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        queued = store.get_job(queued_id)
        if queued is not None and queued["status"] == "succeeded":
            break
        time.sleep(0.01)
    runner.stop()

    assert log[0].startswith("recover:")
    assert log[1] == f"prepare:{queued_id}"


def test_delete_recovery_retries_without_finishing_store_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, job_id, job_dir = queued_store(tmp_path)
    assert store.claim_job(job_id)
    store.fail_job(job_id, code="parse_failed", detail="raw")
    assert store.begin_delete(job_id) == "ok"
    calls = 0
    real_delete = runner_module.delete_job_workspace

    def flaky_delete(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("/private/path token=secret")
        real_delete(path)

    monkeypatch.setattr(runner_module, "delete_job_workspace", flaky_delete)
    runner = _runner(store, FakePipeline(), FakeAtomicIndexer())

    runner.recover_after_restart()
    runner.start()
    time.sleep(0.1)
    runner.stop()
    assert store.get_job(job_id)["status"] == "deleting"
    assert job_dir.exists()

    runner.start()
    runner.recover_after_restart()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and store.get_job(job_id) is not None:
        time.sleep(0.01)
    runner.stop()
    assert store.get_job(job_id) is None
    assert not job_dir.exists()


def test_cleanup_failure_keeps_committed_job_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, job_id, job_dir = queued_store(tmp_path)

    def fail_cleanup(path: Path) -> None:
        raise OSError(f"cannot clean {path} token=secret")

    monkeypatch.setattr(runner_module, "clear_attempt_workspaces", fail_cleanup)
    _runner(store, FakePipeline(), FakeAtomicIndexer()).execute_job(job_id)

    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "running"
    assert job["attempt"]["commit_state"] == "committed"
    assert any((job_dir / "attempts").iterdir())
    assert "secret" not in repr(job)


def test_sqlite_success_failure_restores_committed_journal_for_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, job_id, job_dir = queued_store(tmp_path)
    indexer = FakeAtomicIndexer()
    real_succeed = store.succeed_job
    calls = 0

    def fail_once(job: str, chunk_total: int, warning_count: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("SQLite /private/path token=secret")
        real_succeed(job, chunk_total, warning_count)

    monkeypatch.setattr(store, "succeed_job", fail_once)
    runner = _runner(store, FakePipeline(), indexer)

    runner.execute_job(job_id)

    execution = store.get_job_for_execution(job_id)
    assert execution is not None
    assert execution["status"] == "running"
    assert execution["attempt"]["commit_state"] == "committed"
    journal = execution["attempt"]["journal_path"]
    assert isinstance(journal, Path) and journal.is_file()

    runner.execute_recovery(job_id)
    assert store.get_job(job_id)["status"] == "succeeded"
    assert list((job_dir / "attempts").iterdir()) == []


def test_rolled_back_cleanup_failure_restarts_as_index_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, job_id, _ = queued_store(tmp_path)
    real_cleanup = runner_module.clear_attempt_workspaces
    calls = 0

    def fail_once(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("raw /private/path token=secret")
        real_cleanup(path)

    monkeypatch.setattr(runner_module, "clear_attempt_workspaces", fail_once)
    runner = _runner(store, FakePipeline(), FakeAtomicIndexer(mode="rolled_back"))
    runner.execute_job(job_id)
    assert store.get_job(job_id)["attempt"]["commit_state"] == "rolled_back"

    runner.recover_after_restart()

    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert job["error_code"] == "index_failed"


def test_start_stop_enqueue_are_idempotent_daemon_and_reset_stop(tmp_path: Path) -> None:
    store, job_id, _ = queued_store(tmp_path)
    runner = _runner(store, FakePipeline(), FakeAtomicIndexer())

    runner.start()
    first = runner._thread
    assert first is not None and first.daemon
    runner.start()
    assert runner._thread is first
    runner.enqueue("missing-job")
    runner.enqueue(job_id)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = store.get_job(job_id)
        if job is not None and job["status"] == "succeeded":
            break
        time.sleep(0.01)
    runner.stop()
    runner.stop()
    assert not first.is_alive()

    runner.start()
    assert runner._thread is not None and runner._thread is not first
    assert not runner._stop_event.is_set()
    runner.stop()


def test_stop_during_job_does_not_leave_sentinel_that_kills_restart(tmp_path: Path) -> None:
    store, first_id, _ = queued_store(tmp_path, name="first")
    _, second_id, _ = queued_store(tmp_path, name="second")
    entered = threading.Event()
    release = threading.Event()

    class BlockingPipeline(FakePipeline):
        def prepare(
            self,
            job: dict[str, object],
            workspace: Path,
            *,
            on_event: Callable[[str, dict[str, object]], None] | None = None,
        ) -> PreparedIngestion:
            if str(job["job_id"]) == first_id:
                entered.set()
                assert release.wait(timeout=3)
            return super().prepare(job, workspace, on_event=on_event)

    runner = _runner(store, BlockingPipeline(), FakeAtomicIndexer())
    runner.start()
    runner.enqueue(first_id)
    assert entered.wait(timeout=3)
    stopper = threading.Thread(target=runner.stop)
    stopper.start()
    release.set()
    stopper.join(timeout=3)
    assert not stopper.is_alive()

    runner.start()
    runner.enqueue(second_id)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = store.get_job(second_id)
        if job is not None and job["status"] == "succeeded":
            break
        time.sleep(0.01)
    runner.stop()

    assert store.get_job(second_id)["status"] == "succeeded"


def test_concurrent_stop_enqueues_only_one_sentinel_per_worker_generation(
    tmp_path: Path,
) -> None:
    store, first_id, _ = queued_store(tmp_path, name="first")
    _, second_id, _ = queued_store(tmp_path, name="second")
    entered = threading.Event()
    release = threading.Event()

    class BlockingPipeline(FakePipeline):
        def prepare(
            self,
            job: dict[str, object],
            workspace: Path,
            *,
            on_event: Callable[[str, dict[str, object]], None] | None = None,
        ) -> PreparedIngestion:
            if str(job["job_id"]) == first_id:
                entered.set()
                assert release.wait(timeout=3)
            return super().prepare(job, workspace, on_event=on_event)

    runner = _runner(store, BlockingPipeline(), FakeAtomicIndexer())
    runner.start()
    runner.enqueue(first_id)
    assert entered.wait(timeout=3)
    stoppers = [threading.Thread(target=runner.stop) for _ in range(2)]
    for stopper in stoppers:
        stopper.start()
    time.sleep(0.05)
    queued_stop_count = sum(
        item.kind == "stop" for item in list(runner._queue.queue)
    )
    release.set()
    for stopper in stoppers:
        stopper.join(timeout=3)

    assert queued_stop_count == 1
    runner.start()
    runner.enqueue(second_id)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = store.get_job(second_id)
        if job is not None and job["status"] == "succeeded":
            break
        time.sleep(0.01)
    runner.stop()
    assert store.get_job(second_id)["status"] == "succeeded"


def test_safe_ingestion_error_mapping_never_returns_raw_exception_text() -> None:
    assert safe_ingestion_error("parse_failed") == ("parse_failed", "文档解析失败")
    assert safe_ingestion_error("evil /private/path token=secret") == (
        "storage_unavailable",
        "任务存储暂时不可用",
    )
    assert ingestion_exception_error(AtomicIndexError("向量生成失败")) == (
        "embedding_failed",
        "向量生成失败",
    )
    assert ingestion_exception_error(AtomicIndexError("model raw /private/path token=secret")) == (
        "index_failed",
        "知识库写入失败",
    )
    assert ingestion_exception_error(
        IngestionPipelineError("evil", "raw /private/path token=secret")
    ) == ("storage_unavailable", "任务存储暂时不可用")
