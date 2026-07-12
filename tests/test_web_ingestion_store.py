import sqlite3
from pathlib import Path

import pytest

import xhbx_rag.web.ingestion_store as ingestion_store
from xhbx_rag.web.ingestion_store import IngestionStore
from xhbx_rag.web.ingestion_uploads import PreflightItem, PreflightResult


def _store(tmp_path: Path) -> IngestionStore:
    return IngestionStore(tmp_path / "ingestion.sqlite3")


def _preflight() -> PreflightResult:
    return PreflightResult(
        source_name="cases.zip",
        source_kind="zip",
        target="case",
        items=(
            PreflightItem(1, "case-a", "案例A", ("case-a/a.txt",), 1),
            PreflightItem(2, "case-b", "案例B", ("case-b/b.txt",), 1),
        ),
    )


def _running_job(store: IngestionStore, tmp_path: Path) -> str:
    job = store.create_draft(preflight=_preflight(), source_path=tmp_path / "source.zip")
    job_id = job["job_id"]
    assert store.start_job(job_id) == "ok"
    assert store.claim_job(job_id) is True
    assert store.begin_attempt(job_id, tmp_path / "attempt") == 1
    return job_id


def test_job_lifecycle_requires_valid_state_transitions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = store.create_draft(
        preflight=_preflight(),
        source_path=tmp_path / "jobs" / "job" / "source" / "cases.zip",
    )
    assert job["status"] == "draft"
    assert store.start_job(job["job_id"]) == "ok"
    assert store.start_job(job["job_id"]) == "conflict"
    assert store.claim_job(job["job_id"]) is True
    assert store.claim_job(job["job_id"]) is False
    assert store.begin_attempt(job["job_id"], tmp_path / "attempt") == 1
    store.fail_job(job["job_id"], code="parse_failed", detail="案例解析失败")
    retried = store.retry_job(job["job_id"])
    assert retried == {"result": "ok", "attempt_no": 2}
    assert store.get_job(job["job_id"])["status"] == "queued"


def test_delete_is_two_phase_and_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = store.create_draft(preflight=_preflight(), source_path=tmp_path / "source.zip")
    assert store.begin_delete(job["job_id"]) == "ok"
    assert store.get_job(job["job_id"])["status"] == "deleting"
    assert store.begin_delete(job["job_id"]) == "ok"
    store.finish_delete(job["job_id"])
    assert store.get_job(job["job_id"]) is None


def test_public_and_execution_queries_have_separate_path_shapes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source_path = tmp_path / "jobs" / "job" / "source" / "cases.zip"
    preflight = PreflightResult(
        source_name="cases.zip",
        source_kind="zip",
        target="case",
        items=_preflight().items,
        ignored_entries=("__MACOSX/._a.txt",),
    )
    job = store.create_draft(preflight=preflight, source_path=source_path)

    public = store.get_job(job["job_id"])
    assert public is not None
    assert public["ignored_entries"] == ["__MACOSX/._a.txt"]
    assert public["items"][0]["relative_paths"] == ["case-a/a.txt"]
    assert public["attempt"] is None
    assert "source_path" not in public

    public_keys = {
        "job_id",
        "source_name",
        "source_kind",
        "target",
        "status",
        "current_stage",
        "attempt_count",
        "item_total",
        "item_done",
        "document_total",
        "chunk_total",
        "ignored_total",
        "warning_count",
        "error_code",
        "error_detail",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
    }
    assert set(store.list_jobs()[0]) == public_keys

    assert store.start_job(job["job_id"]) == "ok"
    assert store.claim_job(job["job_id"]) is True
    workspace_path = tmp_path / "jobs" / "job" / "attempts" / "1"
    assert store.begin_attempt(job["job_id"], workspace_path) == 1
    journal_path = workspace_path / "rollback" / "journal.json"
    store.mark_commit_state(job["job_id"], "prepared", journal_path)

    public = store.get_job(job["job_id"])
    assert public is not None
    assert set(public["attempt"]) == {
        "attempt_no",
        "status",
        "current_stage",
        "commit_state",
        "error_code",
        "error_detail",
        "started_at",
        "finished_at",
    }
    assert "workspace_path" not in repr(public)
    assert str(workspace_path) not in repr(public)
    assert str(journal_path) not in repr(public)

    execution = store.get_job_for_execution(job["job_id"])
    assert execution is not None
    assert execution["source_path"] == source_path.resolve()
    assert execution["attempt"]["workspace_path"] == workspace_path.resolve()
    assert execution["attempt"]["journal_path"] == journal_path.resolve()
    assert execution["items"] == public["items"]


def test_attempt_item_progress_and_success_are_persisted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = store.create_draft(preflight=_preflight(), source_path=tmp_path / "source.zip")
    job_id = job["job_id"]
    assert store.start_job(job_id) == "ok"
    assert store.claim_job(job_id) is True
    assert store.begin_attempt(job_id, tmp_path / "attempt") == 1

    store.mark_stage(job_id, "parsing", "正在解析")
    assert store.mark_item_running(job_id, 1, "parsing") is True
    assert store.mark_item_running(job_id, 1, "parsing") is False
    progress = store.get_progress(job_id)
    assert progress is not None
    assert progress == {
        "job_id": job_id,
        "status": "running",
        "current_stage": "parsing",
        "attempt_no": 1,
        "item_total": 2,
        "item_done": 0,
        "document_total": 2,
        "chunk_total": 0,
        "warning_count": 0,
        "active_item_index": 1,
        "message": "开始处理输入项 1",
        "updated_at": progress["updated_at"],
    }

    store.complete_item(job_id, 1, chunk_count=7, warning_count=1)
    assert store.mark_item_running(job_id, 2, "chunking") is True
    store.complete_item(job_id, 2, chunk_count=5, warning_count=2)
    store.mark_stage(job_id, "indexing", "正在入库")
    store.mark_commit_state(job_id, "prepared", tmp_path / "attempt" / "journal.json")
    store.mark_commit_state(job_id, "committed")
    store.succeed_job(job_id, chunk_total=12, warning_count=3)

    completed = store.get_job(job_id)
    assert completed is not None
    assert completed["status"] == "succeeded"
    assert completed["current_stage"] == "completed"
    assert completed["item_done"] == 2
    assert completed["chunk_total"] == 12
    assert completed["warning_count"] == 3
    assert completed["attempt"]["status"] == "succeeded"
    assert [item["status"] for item in completed["items"]] == ["succeeded", "succeeded"]
    assert [item["chunk_count"] for item in completed["items"]] == [7, 5]


def test_item_failure_rollback_and_abort_retry_follow_current_attempt(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = store.create_draft(preflight=_preflight(), source_path=tmp_path / "source.zip")
    job_id = job["job_id"]
    store.start_job(job_id)
    store.claim_job(job_id)
    store.begin_attempt(job_id, tmp_path / "attempt-1")
    assert store.mark_item_running(job_id, 1, "parsing") is True
    store.fail_item(job_id, 1, "案例解析失败")
    store.mark_commit_state(job_id, "prepared", tmp_path / "attempt-1" / "journal.json")
    store.mark_rolling_back(job_id, code="index_failed", detail="写入失败")
    rolling_back = store.get_job(job_id)
    assert rolling_back is not None
    assert rolling_back["status"] == "rolling_back"
    assert rolling_back["attempt"]["status"] == "rolling_back"
    assert rolling_back["attempt"]["commit_state"] == "rolling_back"

    store.mark_commit_state(job_id, "rolled_back")
    store.fail_job(job_id, code="index_failed", detail="写入失败")
    assert store.retry_job(job_id) == {"result": "ok", "attempt_no": 2}
    store.abort_retry(job_id, 2, "无法清理旧任务产物")

    aborted = store.get_job(job_id)
    assert aborted is not None
    assert aborted["status"] == "failed"
    assert aborted["attempt_count"] == 1
    assert aborted["error_detail"] == "无法清理旧任务产物"
    assert aborted["attempt"]["attempt_no"] == 1
    assert store.abort_retry(job_id, 2, "再次调用") is None


def test_recovery_actions_requeue_and_rollback_correct_jobs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    queued = store.create_draft(preflight=_preflight(), source_path=tmp_path / "q.zip")
    store.start_job(queued["job_id"])
    running = store.create_draft(preflight=_preflight(), source_path=tmp_path / "r.zip")
    store.start_job(running["job_id"])
    store.claim_job(running["job_id"])
    store.begin_attempt(running["job_id"], tmp_path / "attempt")
    prepared = store.create_draft(preflight=_preflight(), source_path=tmp_path / "p.zip")
    store.start_job(prepared["job_id"])
    store.claim_job(prepared["job_id"])
    store.begin_attempt(prepared["job_id"], tmp_path / "prepared")
    journal_path = tmp_path / "journal.json"
    store.mark_commit_state(prepared["job_id"], "prepared", journal_path)

    actions = {(item.job_id, item.action, item.journal_path) for item in store.recovery_actions()}

    assert (queued["job_id"], "enqueue", None) in actions
    assert (running["job_id"], "fail_interrupted", None) in actions
    assert (prepared["job_id"], "rollback", journal_path.resolve()) in actions


def test_recovery_actions_include_committed_and_deleting_jobs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    committed = store.create_draft(preflight=_preflight(), source_path=tmp_path / "c.zip")
    store.start_job(committed["job_id"])
    store.claim_job(committed["job_id"])
    store.begin_attempt(committed["job_id"], tmp_path / "committed")
    journal_path = tmp_path / "committed" / "journal.json"
    store.mark_commit_state(committed["job_id"], "prepared", journal_path)
    store.mark_commit_state(committed["job_id"], "committed", journal_path)
    deleting = store.create_draft(preflight=_preflight(), source_path=tmp_path / "d.zip")
    store.begin_delete(deleting["job_id"])

    actions = {(item.job_id, item.action, item.journal_path) for item in store.recovery_actions()}

    assert (committed["job_id"], "finish_committed", journal_path.resolve()) in actions
    assert (deleting["job_id"], "delete", None) in actions


def test_events_are_capped_and_progress_is_compacted_without_losing_milestones(
    tmp_path: Path,
) -> None:
    items = tuple(
        PreflightItem(index, f"case-{index}", f"案例{index}", (f"{index}.txt",), 1)
        for index in range(1, 2_004)
    )
    preflight = PreflightResult("many.zip", "zip", "case", items)
    store = _store(tmp_path)
    job = store.create_draft(preflight=preflight, source_path=tmp_path / "many.zip")
    job_id = job["job_id"]
    store.start_job(job_id)
    store.claim_job(job_id)
    store.begin_attempt(job_id, tmp_path / "attempt")

    for item in items[:-1]:
        assert store.mark_item_running(job_id, item.item_index, "parsing") is True
    store.mark_stage(job_id, "chunking", "开始切分")
    assert store.mark_item_running(job_id, items[-1].item_index, "chunking") is True

    with sqlite3.connect(store.db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM ingestion_events WHERE job_id = ? AND attempt_no = 1",
            (job_id,),
        ).fetchone()[0]
        event_types = {
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM ingestion_events WHERE job_id = ? AND attempt_no = 1",
                (job_id,),
            )
        }
    assert count == 2_000
    assert "attempt_started" in event_types
    assert "progress_compacted" in event_types
    assert "stage_changed" in event_types
    assert store.get_progress(job_id)["message"] == f"开始处理输入项 {items[-1].item_index}"


def test_schema_uses_wal_and_state_conflicts_do_not_mutate_jobs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = store.create_draft(preflight=_preflight(), source_path=tmp_path / "source.zip")
    job_id = job["job_id"]

    with sqlite3.connect(store.db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'ingestion_%'"
            )
        }
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert tables == {
        "ingestion_jobs",
        "ingestion_items",
        "ingestion_attempts",
        "ingestion_events",
    }
    assert journal_mode == "wal"

    assert store.retry_job(job_id) == {"result": "conflict"}
    assert store.begin_delete("missing") == "not_found"
    assert store.start_job(job_id) == "ok"
    assert store.begin_delete(job_id) == "conflict"
    assert store.get_job(job_id)["status"] == "queued"


def test_store_exposes_a_clear_state_conflict_error() -> None:
    assert issubclass(ingestion_store.IngestionStateError, RuntimeError)


def test_succeed_job_rejects_success_without_committed_attempt(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job_id = _running_job(store, tmp_path)

    with pytest.raises(ingestion_store.IngestionStateError):
        store.succeed_job(job_id, chunk_total=1, warning_count=0)

    job = store.get_job(job_id)
    assert job["status"] == "running"
    assert job["attempt"]["status"] == "running"


def test_succeed_job_rejects_success_while_rolling_back(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job_id = _running_job(store, tmp_path)
    store.mark_commit_state(job_id, "prepared", tmp_path / "journal.json")
    store.mark_rolling_back(job_id, code="index_failed", detail="写入失败")

    with pytest.raises(ingestion_store.IngestionStateError):
        store.succeed_job(job_id, chunk_total=1, warning_count=0)

    assert store.get_job(job_id)["status"] == "rolling_back"


@pytest.mark.parametrize("commit_state", ["prepared", "committed"])
def test_fail_job_rejects_failure_after_commit_started(
    tmp_path: Path, commit_state: str
) -> None:
    store = _store(tmp_path)
    job_id = _running_job(store, tmp_path)
    store.mark_commit_state(job_id, "prepared", tmp_path / "journal.json")
    if commit_state == "committed":
        store.mark_commit_state(job_id, "committed")

    with pytest.raises(ingestion_store.IngestionStateError):
        store.fail_job(job_id, code="index_failed", detail="写入失败")

    assert store.get_job(job_id)["status"] == "running"


def test_fail_job_rejects_failure_before_rollback_finishes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job_id = _running_job(store, tmp_path)
    store.mark_commit_state(job_id, "prepared", tmp_path / "journal.json")
    store.mark_rolling_back(job_id, code="index_failed", detail="写入失败")

    with pytest.raises(ingestion_store.IngestionStateError):
        store.fail_job(job_id, code="index_failed", detail="写入失败")

    assert store.get_job(job_id)["status"] == "rolling_back"


def test_prepared_commit_requires_a_journal_path(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job_id = _running_job(store, tmp_path)

    with pytest.raises(ingestion_store.IngestionStateError):
        store.mark_commit_state(job_id, "prepared")

    assert store.get_job(job_id)["attempt"]["commit_state"] == "not_started"


@pytest.mark.parametrize(
    "state",
    ["committed", "rolling_back", "rolled_back", "invalid"],
)
def test_illegal_commit_transitions_are_rejected(tmp_path: Path, state: str) -> None:
    store = _store(tmp_path)
    job_id = _running_job(store, tmp_path)

    with pytest.raises(ingestion_store.IngestionStateError):
        store.mark_commit_state(job_id, state)

    assert store.get_job(job_id)["attempt"]["commit_state"] == "not_started"


def test_fail_job_accepts_only_precommit_or_finished_rollback(tmp_path: Path) -> None:
    store = _store(tmp_path)
    precommit = _running_job(store, tmp_path / "precommit")
    store.fail_job(precommit, code="parse_failed", detail="解析失败")
    assert store.get_job(precommit)["status"] == "failed"

    rolled_back = _running_job(store, tmp_path / "rolled-back")
    store.mark_commit_state(rolled_back, "prepared", tmp_path / "journal.json")
    store.mark_rolling_back(rolled_back, code="index_failed", detail="写入失败")
    store.mark_commit_state(rolled_back, "rolled_back")
    store.fail_job(rolled_back, code="index_failed", detail="写入失败")
    assert store.get_job(rolled_back)["status"] == "failed"


def test_persisted_details_and_event_content_are_safely_normalized(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job_id = _running_job(store, tmp_path)
    secret_value = "sk-live-super-secret-value"
    posix_path = "/Users/alice/private/customer.txt"
    windows_path = r"C:\Users\alice\private\customer.txt"
    unsafe = (
        f"读取 {posix_path} 和 {windows_path} 失败 "
        f"token={secret_value} api_key: {secret_value} secret={secret_value} "
        + "超长错误" * 800
    )

    store.mark_stage(job_id, "parsing", unsafe)
    assert store.mark_item_running(job_id, 1, "parsing") is True
    store.fail_item(job_id, 1, unsafe)
    store.fail_job(job_id, code=f"token={secret_value}", detail=unsafe)

    public = store.get_job(job_id)
    serialized = repr(public)
    for raw_value in (secret_value, posix_path, windows_path):
        assert raw_value not in serialized
    assert len(public["error_code"]) <= 2_000
    assert len(public["error_detail"]) <= 2_000
    assert len(public["items"][0]["error_detail"]) <= 2_000
    assert all(len(event["message"]) <= 2_000 for event in public["events"])

    with sqlite3.connect(store.db_path) as connection:
        raw_values = [
            value
            for row in connection.execute(
                """
                SELECT error_code, error_detail FROM ingestion_jobs WHERE job_id = ?
                UNION ALL
                SELECT NULL, error_detail FROM ingestion_items WHERE job_id = ?
                UNION ALL
                SELECT NULL, message FROM ingestion_events WHERE job_id = ?
                UNION ALL
                SELECT NULL, payload_json FROM ingestion_events WHERE job_id = ?
                """,
                (job_id, job_id, job_id, job_id),
            )
            for value in row
            if value is not None
        ]
    persisted = "\n".join(raw_values)
    for raw_value in (secret_value, posix_path, windows_path):
        assert raw_value not in persisted


def test_event_payload_is_recursively_sanitized_and_limited_to_16_kib(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job_id = _running_job(store, tmp_path)
    secret_value = "nested-super-secret"

    with store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        store._append_event(
            connection,
            job_id,
            1,
            event_type="nested_payload",
            message="读取 /var/private/source.txt",
            payload={
                "nested": {
                    "api_key": secret_value,
                    "values": ["token=" + secret_value, r"C:\private\source.txt"],
                }
            },
        )
        store._append_event(
            connection,
            job_id,
            1,
            event_type="oversized_payload",
            message="超大 payload",
            payload={"values": ["内容" * 1_500 for _ in range(10)]},
        )

    events = store.get_job(job_id)["events"]
    nested = next(event for event in events if event["event_type"] == "nested_payload")
    oversized = next(event for event in events if event["event_type"] == "oversized_payload")
    assert secret_value not in repr(nested)
    assert "/var/private/source.txt" not in repr(nested)
    assert r"C:\private\source.txt" not in repr(nested)
    assert oversized["payload"] == {"truncated": True}

    with sqlite3.connect(store.db_path) as connection:
        payload_json = connection.execute(
            """
            SELECT payload_json FROM ingestion_events
            WHERE job_id = ? AND event_type = 'oversized_payload'
            """,
            (job_id,),
        ).fetchone()[0]
    assert len(payload_json.encode("utf-8")) <= 16 * 1_024


def test_cyclic_event_payload_uses_fixed_truncation_marker(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job_id = _running_job(store, tmp_path)
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    with store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        store._append_event(
            connection,
            job_id,
            1,
            event_type="cyclic_payload",
            message="循环 payload",
            payload=cyclic,
        )

    event = next(
        event
        for event in store.get_job(job_id)["events"]
        if event["event_type"] == "cyclic_payload"
    )
    assert event["payload"] == {"truncated": True}


def test_full_milestone_log_drops_new_event_without_rolling_back_state(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    job_id = _running_job(store, tmp_path)
    for index in range(1_999):
        store.mark_stage(job_id, "parsing", f"解析里程碑 {index}")

    store.mark_stage(job_id, "chunking", "事件已满但阶段仍需提交")

    with sqlite3.connect(store.db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM ingestion_events WHERE job_id = ? AND attempt_no = 1",
            (job_id,),
        ).fetchone()[0]
        attempt_started = connection.execute(
            """
            SELECT COUNT(*) FROM ingestion_events
            WHERE job_id = ? AND attempt_no = 1 AND event_type = 'attempt_started'
            """,
            (job_id,),
        ).fetchone()[0]
    assert count == 2_000
    assert attempt_started == 1
    assert store.get_job(job_id)["current_stage"] == "chunking"
