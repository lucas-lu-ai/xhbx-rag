from pathlib import Path

import logging

import pytest
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


def test_answer_route_uses_default_limits(monkeypatch) -> None:
    calls = {}

    def fake_answer_question(*, query: str, top_n: int, top_k: int):
        calls["query"] = query
        calls["top_n"] = top_n
        calls["top_k"] = top_k
        return {
            "answer": "默认参数已生效。",
            "citations": [],
            "evidence_count": 0,
        }

    monkeypatch.setattr(web_app, "answer_question", fake_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post("/api/answer", json={"query": "保单整理有什么作用？"})

    assert response.status_code == 200
    assert response.json()["answer"] == "默认参数已生效。"
    assert calls == {
        "query": "保单整理有什么作用？",
        "top_n": 20,
        "top_k": 5,
    }


def test_answer_route_rejects_empty_query() -> None:
    client = TestClient(web_app.create_app())

    response = client.post("/api/answer", json={"query": "   "})

    assert response.status_code == 422


def test_answer_route_maps_service_value_error_to_bad_request(monkeypatch) -> None:
    def fail_answer_question(*, query: str, top_n: int, top_k: int):
        raise ValueError("top_k 不能大于 top_n")

    monkeypatch.setattr(web_app, "answer_question", fail_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/answer",
        json={"query": "保单整理有什么作用？", "top_n": 20, "top_k": 5},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "top_k 不能大于 top_n"


def test_answer_route_hides_unknown_value_error_detail_and_logs(
    monkeypatch, caplog
) -> None:
    def fail_answer_question(*, query: str, top_n: int, top_k: int):
        raise ValueError("secret-token leaked from /Users/milan/.env")

    monkeypatch.setattr(web_app, "answer_question", fail_answer_question)
    caplog.set_level(logging.ERROR, logger=web_app.logger.name)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/answer",
        json={"query": "保单整理有什么作用？", "top_n": 20, "top_k": 5},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "问答服务暂时不可用"
    assert "secret-token" not in response.text
    assert "/Users/milan" not in response.text
    assert any(record.message == "Answer route failed" for record in caplog.records)


def test_answer_route_hides_generic_exception_detail_and_logs(monkeypatch) -> None:
    log_messages = []

    class FakeLogger:
        def exception(self, message: str) -> None:
            log_messages.append(message)

    def fail_answer_question(*, query: str, top_n: int, top_k: int):
        raise RuntimeError("secret-token leaked from /Users/milan/.env")

    monkeypatch.setattr(web_app, "logger", FakeLogger(), raising=False)
    monkeypatch.setattr(web_app, "answer_question", fail_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/answer",
        json={"query": "保单整理有什么作用？", "top_n": 20, "top_k": 5},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "问答服务暂时不可用"
    assert "secret-token" not in response.text
    assert "/Users/milan/.env" not in response.text
    assert log_messages == ["Answer route failed"]


@pytest.mark.parametrize("top_n", [True, "20"])
def test_answer_route_rejects_non_strict_top_n(monkeypatch, top_n) -> None:
    def fail_if_called(*, query: str, top_n: int, top_k: int):
        raise AssertionError("answer_question should not be called")

    monkeypatch.setattr(web_app, "answer_question", fail_if_called)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/answer",
        json={"query": "保单整理有什么作用？", "top_n": top_n, "top_k": 5},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("top_k", [True, "5"])
def test_answer_route_rejects_non_strict_top_k(monkeypatch, top_k) -> None:
    def fail_if_called(*, query: str, top_n: int, top_k: int):
        raise AssertionError("answer_question should not be called")

    monkeypatch.setattr(web_app, "answer_question", fail_if_called)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/answer",
        json={"query": "保单整理有什么作用？", "top_n": 20, "top_k": top_k},
    )

    assert response.status_code == 422


def test_answer_route_rejects_top_k_greater_than_top_n(monkeypatch) -> None:
    def fail_if_called(*, query: str, top_n: int, top_k: int):
        raise AssertionError("answer_question should not be called")

    monkeypatch.setattr(web_app, "answer_question", fail_if_called)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/answer",
        json={"query": "保单整理有什么作用？", "top_n": 5, "top_k": 6},
    )

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
        raise web_app.SourcePathError("引用路径必须位于 data 目录内")

    monkeypatch.setattr(web_app, "reveal_in_finder", fail_reveal)
    client = TestClient(web_app.create_app())

    response = client.post("/api/source/reveal", json={"source_path": "../secret.txt"})

    assert response.status_code == 400
    assert response.json()["detail"] == "引用路径必须位于 data 目录内"


def test_reveal_route_hides_value_error_detail_and_logs(monkeypatch, caplog) -> None:
    def fail_reveal(source_path: str):
        raise ValueError("secret-token /Users/milan/data/a.txt")

    monkeypatch.setattr(web_app, "reveal_in_finder", fail_reveal)
    caplog.set_level(logging.ERROR, logger=web_app.logger.name)
    client = TestClient(web_app.create_app())

    response = client.post("/api/source/reveal", json={"source_path": "data/a.txt"})

    assert response.status_code == 500
    assert response.json()["detail"] == "无法在 Finder 中显示文件"
    assert "secret-token" not in response.text
    assert "/Users/milan" not in response.text
    assert any(
        record.message == "Reveal source route failed" for record in caplog.records
    )


def test_reveal_route_hides_generic_exception_detail_and_logs(monkeypatch) -> None:
    log_messages = []

    class FakeLogger:
        def exception(self, message: str) -> None:
            log_messages.append(message)

    def fail_reveal(source_path: str):
        raise RuntimeError("failed for /Users/milan/private.txt with secret-token")

    monkeypatch.setattr(web_app, "logger", FakeLogger(), raising=False)
    monkeypatch.setattr(web_app, "reveal_in_finder", fail_reveal)
    client = TestClient(web_app.create_app())

    response = client.post("/api/source/reveal", json={"source_path": "data/a.txt"})

    assert response.status_code == 500
    assert response.json()["detail"] == "无法在 Finder 中显示文件"
    assert "secret-token" not in response.text
    assert "/Users/milan/private.txt" not in response.text
    assert log_messages == ["Reveal source route failed"]


def test_cors_preflight_allows_localhost_vite_origin() -> None:
    client = TestClient(web_app.create_app())

    response = client.options(
        "/api/answer",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_preflight_rejects_unlisted_origin() -> None:
    client = TestClient(web_app.create_app())

    response = client.options(
        "/api/answer",
        headers={
            "Origin": "http://example.com",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
