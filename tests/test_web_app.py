from pathlib import Path

from fastapi.testclient import TestClient

import xhbx_rag.web.app as web_app


def test_status_route_returns_status(monkeypatch) -> None:
    monkeypatch.setattr(
        web_app,
        "get_status",
        lambda: {
            "ok": True,
            "data_dir": "data",
            "milvus_lite_path": ".local/milvus/xhbx_rag.db",
            "milvus_collection": "xhbx_sales_chunks",
            "config": {"API_KEY": True},
            "errors": [],
        },
    )
    client = TestClient(web_app.create_app())

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_answer_route_returns_answer(monkeypatch) -> None:
    def fake_answer_question(*, query: str, top_n: int, top_k: int):
        assert query == "保单整理有什么作用？"
        assert top_n == 20
        assert top_k == 5
        return {
            "answer": "保单整理能帮助客户看清保障缺口。",
            "citations": [],
            "evidence_count": 0,
        }

    monkeypatch.setattr(web_app, "answer_question", fake_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/answer",
        json={"query": "保单整理有什么作用？", "top_n": 20, "top_k": 5},
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "保单整理能帮助客户看清保障缺口。"


def test_answer_route_rejects_empty_query() -> None:
    client = TestClient(web_app.create_app())

    response = client.post("/api/answer", json={"query": "   "})

    assert response.status_code == 422


def test_reveal_route_calls_finder_reveal(monkeypatch, tmp_path: Path) -> None:
    calls = {}

    def fake_reveal_in_finder(source_path: str):
        calls["source_path"] = source_path
        return tmp_path / "data" / "a.txt"

    monkeypatch.setattr(web_app, "reveal_in_finder", fake_reveal_in_finder)
    client = TestClient(web_app.create_app())

    response = client.post("/api/source/reveal", json={"source_path": "data/a.txt"})

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "resolved_path": str(tmp_path / "data" / "a.txt"),
    }
    assert calls["source_path"] == "data/a.txt"


def test_reveal_route_returns_safe_error(monkeypatch) -> None:
    def fail_reveal(source_path: str):
        raise ValueError("引用路径必须位于 data 目录内")

    monkeypatch.setattr(web_app, "reveal_in_finder", fail_reveal)
    client = TestClient(web_app.create_app())

    response = client.post("/api/source/reveal", json={"source_path": "../secret.txt"})

    assert response.status_code == 400
    assert response.json()["detail"] == "引用路径必须位于 data 目录内"
