from fastapi.testclient import TestClient
import pytest
from uuid import UUID

import xhbx_rag.web.app as web_app
import xhbx_rag.web.a2a_routes as a2a_routes


def test_agent_card_returns_discoverable_answer_agent() -> None:
    client = TestClient(web_app.create_app())

    response = client.get("/a2a/xhbx-rag-answer/.well-known/agent.json")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "name": "xhbx-rag-answer",
        "description": (
            "保险销售知识库问答智能体，接收主控传入的 query，"
            "完成检索、排序、证据约束回答和引用返回。"
        ),
        "url": "http://testserver/a2a/xhbx-rag-answer",
        "version": "1.0.0",
        "capabilities": {"streaming": False},
        "skills": [
            {
                "id": "rag_qa",
                "name": "RAG 知识问答",
                "description": (
                    "基于保险绩优案例和培训课程知识库回答销售相关问题，"
                    "并返回引用证据。"
                ),
            }
        ],
    }


def test_tasks_send_returns_completed_task_with_answer_and_metadata(monkeypatch) -> None:
    calls = {}

    def fake_answer_question(*, query: str, top_n: int, top_k: int) -> dict:
        calls["query"] = query
        calls["top_n"] = top_n
        calls["top_k"] = top_k
        return {
            "answer": "可以先承接预算边界，再引导客户看保障缺口。",
            "citations": [{"source_path": "data/case.txt", "locator": {"line_start": 3}}],
            "evidence_count": 1,
            "retrieval_evidences": [{"chunk_id": "chunk-1", "text": "预算处理话术"}],
        }

    monkeypatch.setattr(a2a_routes, "answer_question", fake_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/a2a/xhbx-rag-answer",
        json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "params": {
                "id": "task-001",
                "sessionId": "session-abc",
                "message": {
                    "role": "user",
                    "parts": [
                        {"type": "text", "text": "客户说每年不能超过80万怎么办？"}
                    ],
                },
                "metadata": {
                    "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
                    "user_id": "10001",
                    "tenant_no": "product",
                    "parent_session_code": "uuid-parent",
                    "ignored": "不会回传",
                },
            },
            "id": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 1
    task = payload["result"]
    assert task["id"] == "task-001"
    assert task["sessionId"] == "session-abc"
    assert task["status"] == {
        "state": "completed",
        "message": {
            "role": "agent",
            "parts": [
                {
                    "type": "text",
                    "text": "可以先承接预算边界，再引导客户看保障缺口。",
                }
            ],
        },
    }
    assert task["metadata"] == {
        "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        "user_id": "10001",
        "tenant_no": "product",
        "parent_session_code": "uuid-parent",
        "evidence_count": 1,
        "citations": [{"source_path": "data/case.txt", "locator": {"line_start": 3}}],
        "retrieval_evidences": [{"chunk_id": "chunk-1", "text": "预算处理话术"}],
    }
    assert calls == {
        "query": "客户说每年不能超过80万怎么办？",
        "top_n": 20,
        "top_k": 5,
    }


def test_tasks_send_generates_task_and_session_ids(monkeypatch) -> None:
    def fake_answer_question(*, query: str, top_n: int, top_k: int) -> dict:
        return {
            "answer": "已回答。",
            "citations": [],
            "evidence_count": 0,
            "retrieval_evidences": [],
        }

    monkeypatch.setattr(a2a_routes, "answer_question", fake_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/a2a/xhbx-rag-answer",
        json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "保单整理有什么作用？"}],
                }
            },
            "id": "rpc-1",
        },
    )

    assert response.status_code == 200
    task = response.json()["result"]
    UUID(task["id"])
    UUID(task["sessionId"])
    assert task["status"]["message"]["parts"][0]["text"] == "已回答。"


def test_tasks_send_rejects_unsupported_method_legacy(monkeypatch) -> None:
    called = False

    def fake_answer_question(*, query: str, top_n: int, top_k: int) -> dict:
        nonlocal called
        called = True
        return {
            "answer": "不会返回。",
            "citations": [],
            "evidence_count": 0,
            "retrieval_evidences": [],
        }

    monkeypatch.setattr(a2a_routes, "answer_question", fake_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/a2a/xhbx-rag-answer",
        json={
            "jsonrpc": "2.0",
            "method": "tasks/sendSubscribe",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "这个不会触发检索"}],
                }
            },
            "id": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {
            "code": -32601,
            "message": "不支持的 A2A 方法: tasks/sendSubscribe",
        },
    }
    assert called is False


def test_extract_query_concatens_text_parts(monkeypatch) -> None:
    calls = {}

    def fake_answer_question(*, query: str, top_n: int, top_k: int) -> dict:
        calls["query"] = query
        return {
            "answer": "已回答。",
            "citations": [],
            "evidence_count": 0,
            "retrieval_evidences": [],
        }

    monkeypatch.setattr(a2a_routes, "answer_question", fake_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/a2a/xhbx-rag-answer",
        json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [
                        {"type": "text", "text": "第一行"},
                        {"type": "image", "image": "..."},  # 不应计入
                        {"type": "text", "text": "第二行"},
                    ],
                }
            },
            "id": 3,
        },
    )

    assert response.status_code == 200
    task = response.json()["result"]
    assert task["status"]["message"]["parts"][0]["text"] == "已回答。"
    assert calls["query"] == "第一行\n第二行"


def test_tasks_send_rejects_empty_query(monkeypatch) -> None:
    calls = 0

    def fake_answer_question(*, query: str, top_n: int, top_k: int) -> dict:
        nonlocal calls
        calls += 1
        return {"answer": "不会调用", "citations": [], "evidence_count": 0}

    monkeypatch.setattr(a2a_routes, "answer_question", fake_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/a2a/xhbx-rag-answer",
        json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "   "}],
                }
            },
            "id": 1,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32602, "message": "问题不能为空"},
    }
    assert calls == 0


def test_tasks_send_rejects_unsupported_method(monkeypatch) -> None:
    calls = 0

    def fake_answer_question(*, query: str, top_n: int, top_k: int) -> dict:
        nonlocal calls
        calls += 1
        return {"answer": "不会调用", "citations": [], "evidence_count": 0}

    monkeypatch.setattr(a2a_routes, "answer_question", fake_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/a2a/xhbx-rag-answer",
        json={
            "jsonrpc": "2.0",
            "method": "tasks/sendSubscribe",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "问题"}],
                }
            },
            "id": "rpc-unsupported",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": "rpc-unsupported",
        "error": {"code": -32601, "message": "不支持的 A2A 方法: tasks/sendSubscribe"},
    }
    assert calls == 0


def test_tasks_send_rejects_invalid_jsonrpc_request() -> None:
    client = TestClient(web_app.create_app())

    response = client.post(
        "/a2a/xhbx-rag-answer",
        json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "问题"}],
                }
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "JSON-RPC 请求格式不合法"},
    }


@pytest.mark.parametrize(
    "payload",
    [[], None, "", "not-object", 123],
)
def test_tasks_send_rejects_invalid_jsonrpc_request_payload_not_object(payload: object) -> None:
    client = TestClient(web_app.create_app())

    response = client.post("/a2a/xhbx-rag-answer", json=payload)

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "JSON-RPC 请求格式不合法"},
    }


def test_tasks_send_rejects_malformed_json_body() -> None:
    client = TestClient(web_app.create_app())

    response = client.post(
        "/a2a/xhbx-rag-answer",
        content=b'{"jsonrpc":"2.0","method":"tasks/send","params":{',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "JSON-RPC 请求格式不合法"},
    }


def test_tasks_send_rejects_non_string_text_part(monkeypatch) -> None:
    called = False

    def fake_answer_question(*, query: str, top_n: int, top_k: int) -> dict:
        nonlocal called
        called = True
        return {
            "answer": "不会返回。",
            "citations": [],
            "evidence_count": 0,
            "retrieval_evidences": [],
        }

    monkeypatch.setattr(a2a_routes, "answer_question", fake_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/a2a/xhbx-rag-answer",
        json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": 123}],
                }
            },
            "id": 4,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 4,
        "error": {"code": -32602, "message": "A2A message.parts[].text 必须是字符串"},
    }
    assert called is False


def test_tasks_send_masks_internal_error(monkeypatch) -> None:
    def fail_answer_question(*, query: str, top_n: int, top_k: int) -> dict:
        raise RuntimeError("secret-token leaked from /Users/milan/.env")

    monkeypatch.setattr(a2a_routes, "answer_question", fail_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/a2a/xhbx-rag-answer",
        json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "保单整理有什么作用？"}],
                }
            },
            "id": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32000, "message": "问答服务暂时不可用"},
    }
    assert "secret-token" not in response.text
    assert "/Users/milan" not in response.text


def test_tasks_send_passes_safe_answer_error(monkeypatch) -> None:
    detail = "本地 Milvus 索引暂时不可用，请关闭其他正在使用索引的进程后重试。"

    def fail_answer_question(*, query: str, top_n: int, top_k: int) -> dict:
        raise ValueError(detail)

    monkeypatch.setattr(a2a_routes, "answer_question", fail_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/a2a/xhbx-rag-answer",
        json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "保单整理有什么作用？"}],
                }
            },
            "id": 1,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32000, "message": detail},
    }


def test_tasks_send_masks_internal_error_when_answer_result_is_not_mapping(monkeypatch) -> None:
    def fail_answer_question(*, query: str, top_n: int, top_k: int) -> str:
        return "not-a-mapping"

    monkeypatch.setattr(a2a_routes, "answer_question", fail_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/a2a/xhbx-rag-answer",
        json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "保单整理有什么作用？"}],
                }
            },
            "id": 1,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32000, "message": "问答服务暂时不可用"},
    }


def test_tasks_send_preserves_zero_ids(monkeypatch) -> None:
    calls = {}

    def fake_answer_question(*, query: str, top_n: int, top_k: int) -> dict:
        calls["query"] = query
        return {
            "answer": "已回答。",
            "citations": [],
            "evidence_count": 0,
            "retrieval_evidences": [],
        }

    monkeypatch.setattr(a2a_routes, "answer_question", fake_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/a2a/xhbx-rag-answer",
        json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "params": {
                "id": 0,
                "sessionId": 0,
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "保单整理有什么作用？"}],
                },
            },
            "id": 0,
        },
    )

    assert response.status_code == 200
    task = response.json()["result"]
    assert task["id"] == 0
    assert task["sessionId"] == 0
    assert task["status"]["message"]["parts"][0]["text"] == "已回答。"
    assert calls["query"] == "保单整理有什么作用？"


def test_tasks_send_generates_ids_for_blank_task_and_session_ids(monkeypatch) -> None:
    calls = 0

    def fake_answer_question(*, query: str, top_n: int, top_k: int) -> dict:
        nonlocal calls
        calls += 1
        return {
            "answer": "已回答。",
            "citations": [],
            "evidence_count": 0,
            "retrieval_evidences": [],
        }

    monkeypatch.setattr(a2a_routes, "answer_question", fake_answer_question)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/a2a/xhbx-rag-answer",
        json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "params": {
                "id": "   ",
                "sessionId": "",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "保单整理有什么作用？"}],
                },
            },
            "id": "rpc-blank-ids",
        },
    )

    assert response.status_code == 200
    task = response.json()["result"]
    UUID(task["id"])
    UUID(task["sessionId"])
    assert task["status"]["message"]["parts"][0]["text"] == "已回答。"
    assert calls == 1
