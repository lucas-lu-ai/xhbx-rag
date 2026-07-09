from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request

from .services import answer_question

AGENT_CODE = "xhbx-rag-answer"
AGENT_PATH = f"/a2a/{AGENT_CODE}"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "保险销售知识库问答智能体，接收主控传入的 query，"
    "完成检索、排序、证据约束回答和引用返回。"
)

router = APIRouter(prefix=AGENT_PATH)
DEFAULT_TOP_N = 20
DEFAULT_TOP_K = 5
JSONRPC_VERSION = "2.0"
PASSTHROUGH_METADATA_KEYS = {
    "traceparent",
    "user_id",
    "tenant_no",
    "parent_session_code",
}


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


@router.post("")
def handle_jsonrpc(payload: dict[str, Any]) -> dict[str, Any]:
    params = payload["params"]
    query = _extract_query(params["message"])
    result = answer_question(query=query, top_n=DEFAULT_TOP_N, top_k=DEFAULT_TOP_K)
    task_id = str(params.get("id") or uuid4())
    session_id = str(params.get("sessionId") or uuid4())
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": payload["id"],
        "result": _completed_task(
            task_id=task_id,
            session_id=session_id,
            answer=result.get("answer", ""),
            request_metadata=params.get("metadata"),
            answer_result=result,
        ),
    }


def _agent_url(request: Request) -> str:
    return f"{str(request.base_url).rstrip('/')}{AGENT_PATH}"


def _completed_task(
    *,
    task_id: str,
    session_id: str,
    answer: str,
    request_metadata: dict[str, Any] | None,
    answer_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": task_id,
        "sessionId": session_id,
        "status": {
            "state": "completed",
            "message": {
                "role": "agent",
                "parts": [{"type": "text", "text": answer}],
            },
        },
        "metadata": {
            **{k: v for k, v in (request_metadata or {}).items() if k in PASSTHROUGH_METADATA_KEYS},
            "citations": answer_result.get("citations", []),
            "evidence_count": answer_result.get("evidence_count", 0),
            "retrieval_evidences": answer_result.get("retrieval_evidences", []),
        },
    }


def _extract_query(message: dict[str, Any]) -> str:
    parts = message["parts"]
    for part in parts:
        if part.get("type") == "text":
            return str(part["text"])
    return ""
