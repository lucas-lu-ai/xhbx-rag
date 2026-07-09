from fastapi.testclient import TestClient
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
