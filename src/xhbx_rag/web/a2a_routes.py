from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

AGENT_CODE = "xhbx-rag-answer"
AGENT_PATH = f"/a2a/{AGENT_CODE}"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "保险销售知识库问答智能体，接收主控传入的 query，"
    "完成检索、排序、证据约束回答和引用返回。"
)

router = APIRouter(prefix=AGENT_PATH)


@router.get("/.well-known/agent.json")
def agent_card(request: Request) -> dict[str, Any]:
    return {
        "name": AGENT_CODE,
        "description": AGENT_DESCRIPTION,
        "url": _agent_url(request),
        "version": AGENT_VERSION,
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


def _agent_url(request: Request) -> str:
    return f"{str(request.base_url).rstrip('/')}{AGENT_PATH}"
