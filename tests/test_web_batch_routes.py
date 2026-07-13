import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import xhbx_rag.web.app as web_app
import xhbx_rag.web.batch_routes as batch_routes
from xhbx_rag.web.batch_store import BatchRunStore


class _FakeRunner:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def start(self) -> None:  # lifespan 兼容
        return

    def stop(self) -> None:
        return

    def enqueue(self, run_id: str) -> None:
        self.enqueued.append(run_id)


def _make_client(tmp_path: Path) -> tuple[TestClient, BatchRunStore, _FakeRunner]:
    store = BatchRunStore(db_path=tmp_path / "batch_runs.sqlite3")
    runner = _FakeRunner()
    client = TestClient(web_app.create_app(batch_store=store, batch_runner=runner))
    return client, store, runner


def _create_payload(**overrides) -> dict:
    payload = {
        "title": "测试批次",
        "source_label": "cases.csv",
        "source_format": "csv",
        "headers": ["问题", "参考答案"],
        "rows": [["问题一", "答案一"], ["问题二", ""]],
        "questions": [
            {
                "row_index": 1,
                "query": "保单整理有什么作用？",
                "input_answer": "答案一",
                "top_n": 20,
                "top_k": 5,
            },
            {"row_index": 2, "query": "客户预算有限怎么办？"},
        ],
    }
    payload.update(overrides)
    return payload


def _bad_case_payload(**overrides) -> dict:
    payload = {
        "query": "保单整理有什么作用？",
        "answer": "保单整理能帮助客户看清保障缺口。",
        "top_n": 20,
        "top_k": 5,
        "issue_types": ["incomplete"],
        "input_answer": "参考答案一",
        "batch_source_label": "cases.csv",
    }
    payload.update(overrides)
    return payload


def _fail_first_row(store: BatchRunStore, run_id: str) -> None:
    store.claim_run(run_id)
    store.mark_row_running(run_id, 1)
    store.fail_row(run_id, 1, "问答服务暂时不可用")
    store.mark_row_running(run_id, 2)
    store.complete_row(run_id, 2, '{"answer": "回答二"}')
    store.finalize_run(run_id)


def test_create_batch_run_returns_201_and_enqueues(tmp_path: Path) -> None:
    client, store, runner = _make_client(tmp_path)

    response = client.post("/api/batch-runs", json=_create_payload())

    assert response.status_code == 201
    entry = response.json()
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
    assert runner.enqueued == [entry["run_id"]]
    assert store.get_run(entry["run_id"]) is not None


def test_create_batch_run_applies_question_defaults(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)

    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]
    detail = client.get(f"/api/batch-runs/{run_id}").json()

    second = detail["questions"][1]
    assert second["input_answer"] == ""
    assert second["top_n"] == 20
    assert second["top_k"] == 5


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_format": "docx"},
        {"debug": True},
        {"source_label": "   "},
        {"title": "长" * 201},
        {"headers": ["h" * 201]},
        {"rows": [["问题一", "答案一"], ["x"] * 51]},
        {
            "questions": [
                {"row_index": 1, "query": "问题一"},
                {"row_index": 1, "query": "问题一重复"},
            ]
        },
        {"questions": [{"row_index": 0, "query": "问题"}]},
        {"questions": [{"row_index": 3, "query": "越界行"}]},
        {"questions": [{"row_index": "1", "query": "非严格整数"}]},
        {"questions": [{"row_index": 1, "query": "   "}]},
        {"questions": [{"row_index": 1, "query": "问题", "top_n": 5, "top_k": 6}]},
        {"questions": []},
        {
            "rows": [["问题"]] * 101,
            "questions": [
                {"row_index": index, "query": f"问题{index}"}
                for index in range(1, 102)
            ],
        },
    ],
)
def test_create_batch_run_rejects_invalid_payload(
    tmp_path: Path, overrides: dict
) -> None:
    client, _, runner = _make_client(tmp_path)

    response = client.post("/api/batch-runs", json=_create_payload(**overrides))

    assert response.status_code == 422
    assert runner.enqueued == []


def test_list_batch_runs_returns_entries(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)
    first = client.post("/api/batch-runs", json=_create_payload(title="批次一"))
    second = client.post("/api/batch-runs", json=_create_payload(title="批次二"))

    response = client.get("/api/batch-runs")

    assert response.status_code == 200
    runs = response.json()["runs"]
    assert [run["run_id"] for run in runs] == [
        second.json()["run_id"],
        first.json()["run_id"],
    ]
    assert runs[0]["question_total"] == 2


def test_get_batch_run_detail_hides_table_by_default(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]

    detail = client.get(f"/api/batch-runs/{run_id}")

    assert detail.status_code == 200
    body = detail.json()
    assert body["run_id"] == run_id
    assert "headers" not in body
    assert "rows" not in body
    first = body["questions"][0]
    assert first["query"] == "保单整理有什么作用？"
    assert first["status"] == "pending"
    assert first["response"] is None
    assert first["error"] is None
    assert first["bad_case"] is None


def test_get_batch_run_detail_includes_table_when_requested(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]

    detail = client.get(f"/api/batch-runs/{run_id}", params={"include_table": "true"})

    body = detail.json()
    assert body["headers"] == ["问题", "参考答案"]
    assert body["rows"] == [["问题一", "答案一"], ["问题二", ""]]


def test_get_batch_run_returns_404_for_unknown_run(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)

    response = client.get("/api/batch-runs/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "批量会话不存在"


def test_progress_route_returns_light_shape(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]

    response = client.get(f"/api/batch-runs/{run_id}/progress")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert body["status"] == "pending"
    assert body["question_total"] == 2
    assert body["question_done"] == 0
    assert body["question_failed"] == 0
    assert [q["row_index"] for q in body["questions"]] == [1, 2]
    assert set(body["questions"][0]) == {"row_index", "status", "updated_at"}


def test_progress_route_returns_404_for_unknown_run(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)

    response = client.get("/api/batch-runs/missing/progress")

    assert response.status_code == 404
    assert response.json()["detail"] == "批量会话不存在"


def test_retry_row_requeues_failed_row(tmp_path: Path) -> None:
    client, store, runner = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]
    runner.enqueued.clear()
    _fail_first_row(store, run_id)

    response = client.post(f"/api/batch-runs/{run_id}/rows/1/retry")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert runner.enqueued == [run_id]
    run = store.get_run(run_id)
    assert run["status"] == "pending"
    assert run["questions"][0]["status"] == "pending"
    assert run["questions"][0]["error"] is None


def test_retry_row_rejects_non_failed_row(tmp_path: Path) -> None:
    client, _, runner = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]
    runner.enqueued.clear()

    response = client.post(f"/api/batch-runs/{run_id}/rows/1/retry")

    assert response.status_code == 409
    assert response.json()["detail"] == "当前状态不允许重试"
    assert runner.enqueued == []


def test_retry_row_returns_404_for_missing_run_and_row(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]

    missing_run = client.post("/api/batch-runs/missing/rows/1/retry")
    missing_row = client.post(f"/api/batch-runs/{run_id}/rows/99/retry")

    assert missing_run.status_code == 404
    assert missing_run.json()["detail"] == "批量会话不存在"
    assert missing_row.status_code == 404
    assert missing_row.json()["detail"] == "批量行不存在"


def test_resume_run_requeues_interrupted_run(tmp_path: Path) -> None:
    client, store, runner = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]
    runner.enqueued.clear()
    store.claim_run(run_id)
    store.recover_after_restart()

    response = client.post(f"/api/batch-runs/{run_id}/resume")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert runner.enqueued == [run_id]
    assert store.get_run(run_id)["status"] == "pending"


def test_resume_run_rejects_non_interrupted_run(tmp_path: Path) -> None:
    client, _, runner = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]
    runner.enqueued.clear()

    response = client.post(f"/api/batch-runs/{run_id}/resume")

    assert response.status_code == 409
    assert response.json()["detail"] == "当前状态不允许继续执行"
    assert runner.enqueued == []


def test_resume_run_returns_404_for_unknown_run(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)

    response = client.post("/api/batch-runs/missing/resume")

    assert response.status_code == 404
    assert response.json()["detail"] == "批量会话不存在"


def test_bad_case_route_double_writes(tmp_path: Path, monkeypatch) -> None:
    client, store, _ = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]
    _fail_first_row(store, run_id)
    saved = {}

    def fake_save_bad_case(payload: dict, *, bad_case_id=None, project_root=None) -> dict:
        saved["payload"] = payload
        saved["bad_case_id"] = bad_case_id
        return {"ok": True, "bad_case_id": bad_case_id, "path": ".local/x.jsonl"}

    monkeypatch.setattr(batch_routes, "save_bad_case", fake_save_bad_case)

    response = client.post(
        f"/api/batch-runs/{run_id}/rows/1/bad-case",
        json=_bad_case_payload(),
    )

    assert response.status_code == 200
    bad_case_id = response.json()["bad_case_id"]
    # bad_case_id 由路由生成，同一 id 同时写入 JSONL 与 SQLite，两侧保持一致。
    assert bad_case_id.startswith("bad-")
    assert saved["bad_case_id"] == bad_case_id
    assert saved["payload"]["run_id"] == run_id
    assert saved["payload"]["row_index"] == 1
    assert saved["payload"]["input_answer"] == "参考答案一"
    assert saved["payload"]["batch_source_label"] == "cases.csv"
    bad_case = store.get_question(run_id, 1)["bad_case"]
    assert bad_case["bad_case_id"] == bad_case_id
    assert bad_case["run_id"] == run_id
    assert bad_case["row_index"] == 1


def test_bad_case_route_saves_two_dimensional_feedback_in_cache(
    tmp_path: Path, monkeypatch
) -> None:
    client, store, _ = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]
    _fail_first_row(store, run_id)

    def fake_save_bad_case(
        payload: dict, *, bad_case_id=None, project_root=None
    ) -> dict:
        return {"ok": True, "bad_case_id": bad_case_id, "path": ".local/x.jsonl"}

    monkeypatch.setattr(batch_routes, "save_bad_case", fake_save_bad_case)
    evidence_feedback = [
        {
            "chunk_id": "case-a-1",
            "retrieval_judgement": "accurate",
            "answer_usage_judgement": "incorrect",
            "reason": "回答误用了该证据。",
        }
    ]

    response = client.post(
        f"/api/batch-runs/{run_id}/rows/1/bad-case",
        json=_bad_case_payload(evidence_feedback=evidence_feedback),
    )

    assert response.status_code == 200
    cached_feedback = store.get_question(run_id, 1)["bad_case"]["evidence_feedback"]
    assert cached_feedback == evidence_feedback


def test_bad_case_route_preserves_legacy_evidence_judgements_in_cache(
    tmp_path: Path, monkeypatch
) -> None:
    client, store, _ = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]
    _fail_first_row(store, run_id)

    def fake_save_bad_case(
        payload: dict, *, bad_case_id=None, project_root=None
    ) -> dict:
        return {"ok": True, "bad_case_id": bad_case_id, "path": ".local/x.jsonl"}

    monkeypatch.setattr(batch_routes, "save_bad_case", fake_save_bad_case)
    evidence_feedback = [
        {"chunk_id": "case-a-1", "judgement": "should_use"},
        {"chunk_id": "case-a-2", "judgement": "should_not_use"},
        {"chunk_id": "case-a-3", "judgement": "ranking_low"},
    ]

    response = client.post(
        f"/api/batch-runs/{run_id}/rows/1/bad-case",
        json=_bad_case_payload(evidence_feedback=evidence_feedback),
    )

    assert response.status_code == 200
    cached_feedback = store.get_question(run_id, 1)["bad_case"]["evidence_feedback"]
    assert cached_feedback == evidence_feedback


def test_bad_case_route_rejects_invalid_two_dimensional_feedback(
    tmp_path: Path,
) -> None:
    client, store, _ = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]
    _fail_first_row(store, run_id)

    response = client.post(
        f"/api/batch-runs/{run_id}/rows/1/bad-case",
        json=_bad_case_payload(
            evidence_feedback=[
                {
                    "chunk_id": "case-a-1",
                    "retrieval_judgement": "inaccurate",
                    "answer_usage_judgement": "correct",
                    "reason": "召回内容无关。",
                }
            ]
        ),
    )

    assert response.status_code == 422
    assert store.get_question(run_id, 1)["bad_case"] is None


def test_bad_case_route_rolls_back_cache_when_jsonl_fails(
    tmp_path: Path, monkeypatch
) -> None:
    client, store, _ = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]
    _fail_first_row(store, run_id)

    def boom(payload: dict, *, bad_case_id=None, project_root=None) -> dict:
        raise OSError("磁盘写入失败")

    monkeypatch.setattr(batch_routes, "save_bad_case", boom)

    response = client.post(
        f"/api/batch-runs/{run_id}/rows/1/bad-case",
        json=_bad_case_payload(),
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "无法保存 bad case"
    # JSONL 落盘失败后补偿清空缓存，避免 SQLite 残留未进入评测数据源的记录。
    assert store.get_question(run_id, 1)["bad_case"] is None


def test_bad_case_route_rejects_non_terminal_row(tmp_path: Path, monkeypatch) -> None:
    client, _, _ = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]

    def fail_if_called(payload: dict, **kwargs) -> dict:
        raise AssertionError("save_bad_case 不应被调用")

    monkeypatch.setattr(batch_routes, "save_bad_case", fail_if_called)

    response = client.post(
        f"/api/batch-runs/{run_id}/rows/1/bad-case",
        json=_bad_case_payload(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "当前状态不允许保存反馈"


def test_bad_case_route_returns_404s(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]

    missing_run = client.post(
        "/api/batch-runs/missing/rows/1/bad-case", json=_bad_case_payload()
    )
    missing_row = client.post(
        f"/api/batch-runs/{run_id}/rows/99/bad-case", json=_bad_case_payload()
    )

    assert missing_run.status_code == 404
    assert missing_run.json()["detail"] == "批量会话不存在"
    assert missing_row.status_code == 404
    assert missing_row.json()["detail"] == "批量行不存在"


@pytest.mark.parametrize(
    "overrides",
    [
        {"issue_types": ["not_allowed"]},
        {"batch_source_label": ""},
        {"input_answer": "x" * 20001},
        {"debug": True},
    ],
)
def test_bad_case_route_rejects_invalid_payload(
    tmp_path: Path, overrides: dict
) -> None:
    client, store, _ = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]
    _fail_first_row(store, run_id)

    response = client.post(
        f"/api/batch-runs/{run_id}/rows/1/bad-case",
        json=_bad_case_payload(**overrides),
    )

    assert response.status_code == 422


def test_delete_run_removes_run(tmp_path: Path) -> None:
    client, store, _ = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]

    response = client.delete(f"/api/batch-runs/{run_id}")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert store.get_run(run_id) is None


def test_delete_run_rejects_running_run(tmp_path: Path) -> None:
    client, store, _ = _make_client(tmp_path)
    run_id = client.post("/api/batch-runs", json=_create_payload()).json()["run_id"]
    store.claim_run(run_id)

    response = client.delete(f"/api/batch-runs/{run_id}")

    assert response.status_code == 409
    assert response.json()["detail"] == "批量任务正在执行，无法删除"


def test_delete_run_returns_404_for_unknown_run(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)

    response = client.delete("/api/batch-runs/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "批量会话不存在"


def test_batch_routes_report_missing_store(tmp_path: Path) -> None:
    client = TestClient(web_app.create_app())

    response = client.post("/api/batch-runs", json=_create_payload())

    assert response.status_code == 500
    assert response.json()["detail"] == "批量任务存储不可用"


def test_batch_routes_hide_sqlite_error_detail(tmp_path: Path, monkeypatch) -> None:
    client, store, _ = _make_client(tmp_path)

    def boom(limit: int = 200) -> list[dict]:
        raise sqlite3.OperationalError("disk I/O error at /Users/milan/secret.db")

    monkeypatch.setattr(store, "list_runs", boom)

    response = client.get("/api/batch-runs")

    assert response.status_code == 500
    assert response.json()["detail"] == "批量任务存储不可用"
    assert "/Users/milan" not in response.text
    assert "secret" not in response.text


def test_cors_preflight_allows_delete_method(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)

    response = client.options(
        "/api/batch-runs/some-id",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "DELETE",
        },
    )

    assert response.status_code == 200
    assert "DELETE" in response.headers["access-control-allow-methods"]
