import threading
import time
from pathlib import Path

from xhbx_rag.web.batch_runner import BatchRunner
from xhbx_rag.web.batch_store import BatchRunStore
from xhbx_rag.web.services import LOCAL_INDEX_UNAVAILABLE_ERROR


def _make_store(tmp_path: Path) -> BatchRunStore:
    return BatchRunStore(db_path=tmp_path / "batch_runs.sqlite3")


def _create_run(store: BatchRunStore, question_count: int = 2) -> str:
    questions = [
        {
            "row_index": index,
            "query": f"问题{index}",
            "input_answer": "",
            "top_n": 20,
            "top_k": 5,
        }
        for index in range(1, question_count + 1)
    ]
    rows = [[f"问题{index}"] for index in range(1, question_count + 1)]
    entry = store.create_run(
        title="测试批次",
        source_label="cases.csv",
        source_format="csv",
        headers=["问题"],
        rows=rows,
        questions=questions,
    )
    return entry["run_id"]


def _serial_runner(store: BatchRunStore, answer_fn) -> BatchRunner:
    return BatchRunner(
        store=store,
        answer_fn=answer_fn,
        concurrency_provider=lambda: 1,
    )


def test_execute_run_completes_all_rows(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    run_id = _create_run(store)
    calls = []

    def answer_fn(query: str, top_n: int, top_k: int) -> dict:
        calls.append((query, top_n, top_k))
        return {"answer": f"回答:{query}", "citations": []}

    _serial_runner(store, answer_fn).execute_run(run_id)

    run = store.get_run(run_id)
    assert run["status"] == "completed"
    assert [q["status"] for q in run["questions"]] == ["succeeded", "succeeded"]
    assert run["questions"][0]["response"] == {"answer": "回答:问题1", "citations": []}
    assert run["question_done"] == 2
    assert run["question_failed"] == 0
    assert calls == [("问题1", 20, 5), ("问题2", 20, 5)]


def test_execute_run_isolates_row_failures(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    run_id = _create_run(store)

    def answer_fn(query: str, top_n: int, top_k: int) -> dict:
        if query == "问题1":
            raise RuntimeError("boom")
        return {"answer": "回答"}

    _serial_runner(store, answer_fn).execute_run(run_id)

    run = store.get_run(run_id)
    assert run["status"] == "completed"
    assert [q["status"] for q in run["questions"]] == ["failed", "succeeded"]
    assert run["question_done"] == 1
    assert run["question_failed"] == 1


def test_execute_run_sanitizes_row_error(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    run_id = _create_run(store, question_count=1)

    def answer_fn(query: str, top_n: int, top_k: int) -> dict:
        raise RuntimeError("failed at /Users/xxx/.env with secret-token")

    _serial_runner(store, answer_fn).execute_run(run_id)

    question = store.get_question(run_id, 1)
    assert question["status"] == "failed"
    assert question["error"] == "问答服务暂时不可用"
    assert "secret-token" not in question["error"]
    assert "/Users/xxx" not in question["error"]


def test_execute_run_preserves_local_index_error(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    run_id = _create_run(store, question_count=1)

    def answer_fn(query: str, top_n: int, top_k: int) -> dict:
        raise ValueError(LOCAL_INDEX_UNAVAILABLE_ERROR)

    _serial_runner(store, answer_fn).execute_run(run_id)

    question = store.get_question(run_id, 1)
    assert question["status"] == "failed"
    assert question["error"] == LOCAL_INDEX_UNAVAILABLE_ERROR


def test_execute_run_marks_interrupted_on_run_level_failure(
    tmp_path: Path, monkeypatch
) -> None:
    store = _make_store(tmp_path)
    run_id = _create_run(store)
    runner = _serial_runner(store, lambda query, top_n, top_k: {"answer": "回答"})

    def boom(run_id: str) -> list[int]:
        raise RuntimeError("db broken")

    monkeypatch.setattr(store, "fetch_pending_row_indexes", boom)

    runner.execute_run(run_id)

    run = store.get_run(run_id)
    assert run["status"] == "interrupted"
    assert [q["status"] for q in run["questions"]] == ["pending", "pending"]


def test_execute_run_skips_deleted_run(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    run_id = _create_run(store)
    assert store.delete_run(run_id) == "ok"

    _serial_runner(store, lambda query, top_n, top_k: {"answer": "回答"}).execute_run(
        run_id
    )

    assert store.get_run(run_id) is None


def test_execute_run_runs_rows_in_parallel_when_concurrency_allows(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    run_id = _create_run(store, question_count=2)
    barrier = threading.Barrier(2, timeout=5)

    def answer_fn(query: str, top_n: int, top_k: int) -> dict:
        # 若串行执行，barrier 将超时抛错并把行标记为失败，从而暴露问题。
        barrier.wait()
        return {"answer": "回答"}

    runner = BatchRunner(
        store=store,
        answer_fn=answer_fn,
        concurrency_provider=lambda: 2,
    )
    runner.execute_run(run_id)

    run = store.get_run(run_id)
    assert run["status"] == "completed"
    assert [q["status"] for q in run["questions"]] == ["succeeded", "succeeded"]


def test_worker_thread_processes_enqueued_runs(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    run_id = _create_run(store)
    runner = BatchRunner(
        store=store,
        answer_fn=lambda query, top_n, top_k: {"answer": "回答"},
        concurrency_provider=lambda: 1,
    )

    runner.start()
    runner.start()  # start 幂等
    try:
        runner.enqueue(run_id)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if store.get_run(run_id)["status"] == "completed":
                break
            time.sleep(0.01)
        assert store.get_run(run_id)["status"] == "completed"
    finally:
        runner.stop()
        runner.stop()  # stop 幂等
