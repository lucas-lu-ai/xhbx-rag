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
            "milvus_mode": "lite",
            "milvus_target": ".local/milvus/xhbx_rag.db",
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


def test_answer_route_allows_safe_missing_config_keys(monkeypatch) -> None:
    def fail_answer_question(*, query: str, top_n: int, top_k: int):
        raise ValueError("缺少必要环境变量: API_KEY, RERANK_API_KEY")

    monkeypatch.setattr(web_app, "answer_question", fail_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/answer",
        json={"query": "保单整理有什么作用？", "top_n": 20, "top_k": 5},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "缺少必要环境变量: API_KEY, RERANK_API_KEY"


def test_answer_route_hides_tampered_missing_config_error_and_logs(
    monkeypatch, caplog
) -> None:
    def fail_answer_question(*, query: str, top_n: int, top_k: int):
        raise ValueError("缺少必要环境变量: API_KEY at /Users/milan/.env secret-token")

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


def test_answer_route_reports_local_index_unavailable(monkeypatch) -> None:
    detail = "本地 Milvus 索引暂时不可用，请关闭其他正在使用索引的进程后重试。"

    def fail_answer_question(*, query: str, top_n: int, top_k: int):
        raise ValueError(detail)

    monkeypatch.setattr(web_app, "answer_question", fail_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/answer",
        json={"query": "保单整理有什么作用？", "top_n": 20, "top_k": 5},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == detail


def test_answer_stream_route_streams_sse_events(monkeypatch) -> None:
    calls = {}

    def fake_answer_question_stream_events(*, query, top_n, top_k):
        calls["query"] = query
        calls["top_n"] = top_n
        calls["top_k"] = top_k
        yield {
            "type": "step",
            "step": "search.query_understood",
            "message": "已完成问题理解",
            "payload": {"rewritten_query": "客户预算上限80万时如何回应"},
        }
        yield {"type": "answer_delta", "text": "先承接预算。"}
        yield {
            "type": "final",
            "response": {
                "answer": "先承接预算。",
                "citations": [],
                "evidence_count": 0,
                "retrieval_evidences": [],
            },
        }

    monkeypatch.setattr(
        web_app,
        "answer_question_stream_events",
        fake_answer_question_stream_events,
    )
    client = TestClient(web_app.create_app())

    with client.stream(
        "POST",
        "/api/answer/stream",
        json={"query": "客户说每年不能超过80万怎么办？", "top_n": 20, "top_k": 5},
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert calls == {
        "query": "客户说每年不能超过80万怎么办？",
        "top_n": 20,
        "top_k": 5,
    }
    assert "event: step" in body
    assert '"message": "已完成问题理解"' in body
    assert "event: answer_delta" in body
    assert '"text": "先承接预算。"' in body
    assert "event: final" in body


def test_answer_stream_route_hides_internal_error_detail_and_logs(monkeypatch) -> None:
    log_messages = []

    class FakeLogger:
        def exception(self, message: str) -> None:
            log_messages.append(message)

    def fake_answer_question_stream_events(*, query, top_n, top_k):
        yield {
            "type": "_exception",
            "exception": RuntimeError("secret-token leaked from /Users/milan/.env"),
        }

    monkeypatch.setattr(web_app, "logger", FakeLogger(), raising=False)
    monkeypatch.setattr(
        web_app,
        "answer_question_stream_events",
        fake_answer_question_stream_events,
    )
    client = TestClient(web_app.create_app())

    with client.stream(
        "POST",
        "/api/answer/stream",
        json={"query": "客户说每年不能超过80万怎么办？"},
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    assert "event: error" in body
    assert "问答服务暂时不可用" in body
    assert "secret-token" not in body
    assert "/Users/milan" not in body
    assert log_messages == ["Answer stream route failed"]


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


def test_answer_route_rejects_extra_fields(monkeypatch) -> None:
    def fail_if_called(*, query: str, top_n: int, top_k: int):
        raise AssertionError("answer_question should not be called")

    monkeypatch.setattr(web_app, "answer_question", fail_if_called)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/answer",
        json={
            "query": "保单整理有什么作用？",
            "top_n": 20,
            "top_k": 5,
            "debug": True,
        },
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
    assert (
        response.json()["detail"]
        == "无法显示引用文件，请确认文件位于 data 目录内且仍然存在。"
    )


def test_reveal_route_hides_missing_source_path_detail() -> None:
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/source/reveal",
        json={"source_path": "data/__missing_task4_review__.txt"},
    )

    assert response.status_code == 400
    assert (
        response.json()["detail"]
        == "无法显示引用文件，请确认文件位于 data 目录内且仍然存在。"
    )
    assert "/Users" not in response.text
    assert "xhbx-rag" not in response.text
    assert "__missing_task4_review__" not in response.text


def test_reveal_route_rejects_extra_fields(monkeypatch) -> None:
    def fail_if_called(source_path: str):
        raise AssertionError("reveal_in_finder should not be called")

    monkeypatch.setattr(web_app, "reveal_in_finder", fail_if_called)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/source/reveal",
        json={"source_path": "data/a.txt", "debug": True},
    )

    assert response.status_code == 422


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


def test_bad_case_route_saves_payload(monkeypatch) -> None:
    calls = {}

    def fake_save_bad_case(payload: dict):
        calls["payload"] = payload
        return {"ok": True, "bad_case_id": "bad-case-1", "path": ".local/x.jsonl"}

    monkeypatch.setattr(web_app, "save_bad_case", fake_save_bad_case)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/bad-cases",
        json={
            "query": "保单整理对客户有什么作用？",
            "rewritten_query": "保单整理客户价值",
            "answer": "保单整理能帮助客户看清保障缺口。",
            "top_n": 20,
            "top_k": 5,
            "feedback_result": "incomplete",
            "problem_tags": ["missing_talk_track"],
            "problem_detail": "当前回答没有讲清楚保障缺口。",
            "expected_answer": "应该命中保障缺口分析的案例片段。",
            "reference_note": "案例A 第3节",
            "evidence_feedback": [
                {
                    "chunk_id": "case-a-1",
                    "judgement": "should_use",
                    "label": "案例A · 需求分析",
                    "text_preview": "先做保单整理。",
                }
            ],
            "issue_types": ["incomplete", "missing_talk_track"],
            "expected_knowledge": "应该命中保障缺口分析的案例片段。",
            "expected_source": "案例A 第3节",
            "note": "当前回答没有讲清楚保障缺口。",
            "citations": [{"filename": "第1节.track-0.txt"}],
            "retrieval_evidences": [{"chunk_id": "case-a-1"}],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "bad_case_id": "bad-case-1"}
    assert calls["payload"]["query"] == "保单整理对客户有什么作用？"
    assert calls["payload"]["top_n"] == 20
    assert calls["payload"]["feedback_result"] == "incomplete"
    assert calls["payload"]["problem_tags"] == ["missing_talk_track"]
    assert calls["payload"]["issue_types"] == ["incomplete", "missing_talk_track"]
    assert calls["payload"]["evidence_feedback"][0]["judgement"] == "should_use"
    assert calls["payload"]["retrieval_evidences"] == [{"chunk_id": "case-a-1"}]


def test_bad_case_route_accepts_ranking_low_evidence_judgement(monkeypatch) -> None:
    calls = {}

    def fake_save_bad_case(payload: dict):
        calls["payload"] = payload
        return {"ok": True, "bad_case_id": "bad-case-2", "path": ".local/x.jsonl"}

    monkeypatch.setattr(web_app, "save_bad_case", fake_save_bad_case)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/bad-cases",
        json={
            "query": "保单整理对客户有什么作用？",
            "answer": "answer",
            "top_n": 20,
            "top_k": 5,
            "issue_types": ["ranking_wrong"],
            "evidence_feedback": [
                {
                    "chunk_id": "case-a-1",
                    "judgement": "ranking_low",
                    "label": "案例A · 需求分析",
                    "text_preview": "先做保单整理。",
                }
            ],
            "citations": [],
            "retrieval_evidences": [],
        },
    )

    assert response.status_code == 200
    assert calls["payload"]["evidence_feedback"][0]["judgement"] == "ranking_low"


def test_bad_case_route_rejects_unknown_evidence_judgement(monkeypatch) -> None:
    def fail_if_called(payload: dict):
        raise AssertionError("save_bad_case should not be called")

    monkeypatch.setattr(web_app, "save_bad_case", fail_if_called)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/bad-cases",
        json={
            "query": "保单整理对客户有什么作用？",
            "answer": "answer",
            "top_n": 20,
            "top_k": 5,
            "issue_types": ["other"],
            "evidence_feedback": [
                {"chunk_id": "case-a-1", "judgement": "not_allowed"}
            ],
            "citations": [],
            "retrieval_evidences": [],
        },
    )

    assert response.status_code == 422


def test_bad_case_route_rejects_unknown_issue_type(monkeypatch) -> None:
    def fail_if_called(payload: dict):
        raise AssertionError("save_bad_case should not be called")

    monkeypatch.setattr(web_app, "save_bad_case", fail_if_called)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/bad-cases",
        json={
            "query": "保单整理对客户有什么作用？",
            "answer": "answer",
            "top_n": 20,
            "top_k": 5,
            "issue_types": ["not_allowed"],
            "citations": [],
            "retrieval_evidences": [],
        },
    )

    assert response.status_code == 422


def test_bad_case_route_hides_storage_error_detail_and_logs(monkeypatch) -> None:
    log_messages = []

    class FakeLogger:
        def exception(self, message: str) -> None:
            log_messages.append(message)

    def fail_save_bad_case(payload: dict):
        raise RuntimeError("failed writing /Users/milan/private.txt secret-token")

    monkeypatch.setattr(web_app, "logger", FakeLogger(), raising=False)
    monkeypatch.setattr(web_app, "save_bad_case", fail_save_bad_case)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/bad-cases",
        json={
            "query": "保单整理对客户有什么作用？",
            "answer": "answer",
            "top_n": 20,
            "top_k": 5,
            "issue_types": ["other"],
            "citations": [],
            "retrieval_evidences": [],
        },
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "无法保存 bad case"
    assert "secret-token" not in response.text
    assert "/Users/milan" not in response.text
    assert log_messages == ["Bad case route failed"]


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
