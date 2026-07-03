import sqlite3
from datetime import datetime
from pathlib import Path

import xhbx_rag.web.batch_store as batch_store_module
from xhbx_rag.web.batch_store import BatchRunStore


def _make_store(tmp_path: Path) -> BatchRunStore:
    return BatchRunStore(db_path=tmp_path / "batch_runs.sqlite3")


def _questions() -> list[dict]:
    return [
        {
            "row_index": 1,
            "query": "保单整理有什么作用？",
            "input_answer": "参考答案一",
            "top_n": 20,
            "top_k": 5,
        },
        {
            "row_index": 2,
            "query": "客户预算有限怎么办？",
            "input_answer": "",
            "top_n": 10,
            "top_k": 3,
        },
    ]


def _create_run(store: BatchRunStore, **overrides) -> dict:
    params = {
        "title": "测试批次",
        "source_label": "cases.csv",
        "source_format": "csv",
        "headers": ["问题", "参考答案"],
        "rows": [["保单整理有什么作用？", "参考答案一"], ["客户预算有限怎么办？", ""]],
        "questions": _questions(),
    }
    params.update(overrides)
    return store.create_run(**params)


def test_create_run_returns_listing_entry(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    entry = _create_run(store)

    assert entry["run_id"]
    assert entry["title"] == "测试批次"
    assert entry["status"] == "pending"
    assert entry["source_label"] == "cases.csv"
    assert entry["source_format"] == "csv"
    assert entry["question_total"] == 2
    assert entry["question_done"] == 0
    assert entry["question_failed"] == 0
    assert entry["created_at"]
    assert entry["updated_at"]


def test_create_run_timestamps_are_timezone_aware(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    entry = _create_run(store)

    created_at = datetime.fromisoformat(entry["created_at"])
    updated_at = datetime.fromisoformat(entry["updated_at"])
    assert created_at.tzinfo is not None
    assert updated_at.tzinfo is not None


def test_store_uses_wal_journal_mode(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    with sqlite3.connect(str(store.db_path)) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert mode == "wal"


def test_get_run_returns_detail_without_table_by_default(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)

    run = store.get_run(entry["run_id"])

    assert run["run_id"] == entry["run_id"]
    assert run["status"] == "pending"
    assert "headers" not in run
    assert "rows" not in run
    assert [q["row_index"] for q in run["questions"]] == [1, 2]
    first = run["questions"][0]
    assert first["query"] == "保单整理有什么作用？"
    assert first["input_answer"] == "参考答案一"
    assert first["top_n"] == 20
    assert first["top_k"] == 5
    assert first["status"] == "pending"
    assert first["response"] is None
    assert first["error"] is None
    assert first["bad_case"] is None
    assert first["updated_at"]


def test_get_run_returns_table_when_requested(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)

    run = store.get_run(entry["run_id"], include_table=True)

    assert run["headers"] == ["问题", "参考答案"]
    assert run["rows"] == [
        ["保单整理有什么作用？", "参考答案一"],
        ["客户预算有限怎么办？", ""],
    ]


def test_get_run_returns_none_for_unknown_run(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    assert store.get_run("missing") is None


def test_list_runs_orders_desc_and_limits(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    first = _create_run(store, title="批次一")
    second = _create_run(store, title="批次二")
    third = _create_run(store, title="批次三")

    runs = store.list_runs()
    limited = store.list_runs(limit=2)

    assert [run["run_id"] for run in runs] == [
        third["run_id"],
        second["run_id"],
        first["run_id"],
    ]
    assert len(limited) == 2
    assert limited[0]["title"] == "批次三"


def test_get_progress_returns_light_shape(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)

    progress = store.get_progress(entry["run_id"])

    assert progress["run_id"] == entry["run_id"]
    assert progress["status"] == "pending"
    assert progress["question_total"] == 2
    assert progress["question_done"] == 0
    assert progress["question_failed"] == 0
    assert progress["updated_at"]
    assert progress["questions"] == [
        {"row_index": 1, "status": "pending", "updated_at": progress["questions"][0]["updated_at"]},
        {"row_index": 2, "status": "pending", "updated_at": progress["questions"][1]["updated_at"]},
    ]


def test_get_progress_returns_none_for_unknown_run(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    assert store.get_progress("missing") is None


def test_claim_run_transitions_pending_to_running(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)

    assert store.claim_run(entry["run_id"]) is True
    assert store.get_run(entry["run_id"])["status"] == "running"
    assert store.claim_run(entry["run_id"]) is False
    assert store.claim_run("missing") is False


def test_fetch_pending_row_indexes(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)

    assert store.fetch_pending_row_indexes(entry["run_id"]) == [1, 2]

    store.mark_row_running(entry["run_id"], 1)

    assert store.fetch_pending_row_indexes(entry["run_id"]) == [2]


def test_row_lifecycle_success(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)
    run_id = entry["run_id"]

    assert store.mark_row_running(run_id, 1) is True
    store.complete_row(run_id, 1, '{"answer": "回答一"}')

    run = store.get_run(run_id)
    first = run["questions"][0]
    assert first["status"] == "succeeded"
    assert first["response"] == {"answer": "回答一"}
    assert first["error"] is None
    assert run["question_done"] == 1
    assert run["question_failed"] == 0


def test_row_lifecycle_failure(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)
    run_id = entry["run_id"]

    store.mark_row_running(run_id, 2)
    store.fail_row(run_id, 2, "问答服务暂时不可用")

    run = store.get_run(run_id)
    second = run["questions"][1]
    assert second["status"] == "failed"
    assert second["error"] == "问答服务暂时不可用"
    assert second["response"] is None
    assert run["question_done"] == 0
    assert run["question_failed"] == 1


def test_mark_row_running_requires_pending_row(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)
    run_id = entry["run_id"]
    store.mark_row_running(run_id, 1)
    store.complete_row(run_id, 1, "{}")

    assert store.mark_row_running(run_id, 1) is False
    assert store.mark_row_running(run_id, 99) is False
    assert store.mark_row_running("missing", 1) is False


def test_row_update_refreshes_row_and_run_updated_at(tmp_path: Path, monkeypatch) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)
    run_id = entry["run_id"]
    fixed_time = "2026-07-02T00:00:01+00:00"
    monkeypatch.setattr(batch_store_module, "_utc_now_iso", lambda: fixed_time)

    store.mark_row_running(run_id, 1)

    run = store.get_run(run_id)
    assert run["updated_at"] == fixed_time
    assert run["questions"][0]["updated_at"] == fixed_time
    assert run["questions"][1]["updated_at"] != fixed_time


def test_get_question_returns_row_detail(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)

    question = store.get_question(entry["run_id"], 2)

    assert question["row_index"] == 2
    assert question["query"] == "客户预算有限怎么办？"
    assert question["top_n"] == 10
    assert question["top_k"] == 3
    assert question["status"] == "pending"
    assert store.get_question(entry["run_id"], 99) is None
    assert store.get_question("missing", 1) is None


def test_finalize_run_completes_when_no_active_rows(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)
    run_id = entry["run_id"]
    store.claim_run(run_id)
    store.mark_row_running(run_id, 1)
    store.complete_row(run_id, 1, "{}")
    store.mark_row_running(run_id, 2)
    store.fail_row(run_id, 2, "问答服务暂时不可用")

    store.finalize_run(run_id)

    assert store.get_run(run_id)["status"] == "completed"


def test_finalize_run_keeps_running_when_rows_pending(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)
    run_id = entry["run_id"]
    store.claim_run(run_id)
    store.mark_row_running(run_id, 1)
    store.complete_row(run_id, 1, "{}")

    store.finalize_run(run_id)

    assert store.get_run(run_id)["status"] == "running"


def test_finalize_run_keeps_interrupted_run(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)
    run_id = entry["run_id"]
    store.claim_run(run_id)
    store.recover_after_restart()

    store.finalize_run(run_id)

    assert store.get_run(run_id)["status"] == "interrupted"


def test_retry_row_resets_failed_row_and_run(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)
    run_id = entry["run_id"]
    store.claim_run(run_id)
    store.mark_row_running(run_id, 1)
    store.complete_row(run_id, 1, '{"answer": "回答一"}')
    store.mark_row_running(run_id, 2)
    store.fail_row(run_id, 2, "问答服务暂时不可用")
    store.save_row_bad_case(run_id, 2, '{"bad_case_id": "bad-1"}')
    store.finalize_run(run_id)

    result = store.retry_row(run_id, 2)

    assert result == "ok"
    run = store.get_run(run_id)
    assert run["status"] == "pending"
    retried = run["questions"][1]
    assert retried["status"] == "pending"
    assert retried["response"] is None
    assert retried["error"] is None
    assert retried["bad_case"] is None
    untouched = run["questions"][0]
    assert untouched["status"] == "succeeded"
    assert untouched["response"] == {"answer": "回答一"}


def test_retry_row_rejects_non_failed_row(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)

    assert store.retry_row(entry["run_id"], 1) == "conflict"


def test_retry_row_reports_missing_run_and_row(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)

    assert store.retry_row("missing", 1) == "run_not_found"
    assert store.retry_row(entry["run_id"], 99) == "row_not_found"


def test_retry_row_rejects_running_run(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)
    run_id = entry["run_id"]
    store.claim_run(run_id)  # run -> running
    store.mark_row_running(run_id, 1)
    store.fail_row(run_id, 1, "问答服务暂时不可用")  # 行 failed，run 仍 running

    # 执行中的 run 不允许重试：否则会把 run 改回 pending，绕过删除防护。
    assert store.retry_row(run_id, 1) == "conflict"
    assert store.get_run(run_id)["status"] == "running"


def test_mark_run_interrupted_resets_running_rows(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)
    run_id = entry["run_id"]
    store.claim_run(run_id)
    store.mark_row_running(run_id, 1)  # 行 1 卡在 running

    store.mark_run_interrupted(run_id)

    run = store.get_run(run_id)
    assert run["status"] == "interrupted"
    # 与 recover_after_restart 对称：卡在 running 的行回退为 pending，
    # 否则 resume 后 finalize 永远无法把 run 推进到 completed。
    assert run["questions"][0]["status"] == "pending"
    assert store.resume_run(run_id) == "ok"
    assert store.claim_run(run_id) is True
    assert 1 in store.fetch_pending_row_indexes(run_id)


def test_resume_run_requires_interrupted_status(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)
    run_id = entry["run_id"]

    assert store.resume_run(run_id) == "conflict"
    assert store.resume_run("missing") == "run_not_found"

    store.claim_run(run_id)
    store.recover_after_restart()

    assert store.resume_run(run_id) == "ok"
    assert store.get_run(run_id)["status"] == "pending"


def test_save_row_bad_case_requires_terminal_row(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)
    run_id = entry["run_id"]

    assert store.save_row_bad_case(run_id, 1, "{}") == "conflict"
    assert store.save_row_bad_case("missing", 1, "{}") == "run_not_found"
    assert store.save_row_bad_case(run_id, 99, "{}") == "row_not_found"

    store.mark_row_running(run_id, 1)
    store.complete_row(run_id, 1, "{}")

    assert (
        store.save_row_bad_case(run_id, 1, '{"bad_case_id": "bad-1", "row_index": 1}')
        == "ok"
    )
    question = store.get_question(run_id, 1)
    assert question["bad_case"] == {"bad_case_id": "bad-1", "row_index": 1}


def test_delete_run_removes_run_and_rows(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)
    other = _create_run(store, title="保留批次")
    run_id = entry["run_id"]

    assert store.delete_run(run_id) == "ok"
    assert store.get_run(run_id) is None
    assert store.get_question(run_id, 1) is None
    assert store.get_run(other["run_id"]) is not None


def test_delete_run_rejects_running_and_missing(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = _create_run(store)
    store.claim_run(entry["run_id"])

    assert store.delete_run(entry["run_id"]) == "conflict"
    assert store.delete_run("missing") == "run_not_found"


def test_recover_after_restart_resets_active_state(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    running = _create_run(store, title="执行中")
    pending = _create_run(store, title="等待中")
    completed = _create_run(store, title="已完成")
    store.claim_run(running["run_id"])
    store.mark_row_running(running["run_id"], 1)
    store.claim_run(completed["run_id"])
    store.mark_row_running(completed["run_id"], 1)
    store.complete_row(completed["run_id"], 1, "{}")
    store.mark_row_running(completed["run_id"], 2)
    store.complete_row(completed["run_id"], 2, "{}")
    store.finalize_run(completed["run_id"])

    store.recover_after_restart()

    recovered = store.get_run(running["run_id"])
    assert recovered["status"] == "interrupted"
    assert recovered["questions"][0]["status"] == "pending"
    assert store.get_run(pending["run_id"])["status"] == "interrupted"
    assert store.get_run(completed["run_id"])["status"] == "completed"
