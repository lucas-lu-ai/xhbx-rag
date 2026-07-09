from fastapi.testclient import TestClient

import xhbx_rag.web.app as web_app


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
