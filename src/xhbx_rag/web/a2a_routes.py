from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request

from .safe_errors import answer_exception_detail
from .services import answer_question

logger = logging.getLogger(__name__)

AGENT_CODE = "xhbx-rag-answer"
AGENT_PATH = f"/a2a/{AGENT_CODE}"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "保险销售知识库问答智能体，接收主控传入的 query，"
    "完成检索、排序、证据约束回答和引用返回。"
)
DEFAULT_TOP_N = 20
DEFAULT_TOP_K = 5
JSONRPC_VERSION = "2.0"
SUPPORTED_METHOD = "tasks/send"
PASSTHROUGH_METADATA_KEYS = {
    "traceparent",
    "user_id",
    "tenant_no",
    "parent_session_code",
}
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
SERVER_ERROR = -32000

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


@router.post("")
async def handle_jsonrpc(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - malformed JSON must map to JSON-RPC error
        return _jsonrpc_error(None, INVALID_REQUEST, "JSON-RPC 请求格式不合法")

    request_id = payload.get("id") if isinstance(payload, dict) else None
    if not _is_valid_jsonrpc_envelope(payload):
        return _jsonrpc_error(request_id, INVALID_REQUEST, "JSON-RPC 请求格式不合法")

    method = payload.get("method")
    if method != SUPPORTED_METHOD:
        return _jsonrpc_error(
            request_id,
            METHOD_NOT_FOUND,
            f"不支持的 A2A 方法: {method}",
        )

    params = payload.get("params")
    if not isinstance(params, dict):
        return _jsonrpc_error(request_id, INVALID_PARAMS, "A2A 参数格式不合法")

    try:
        query = _extract_query(params.get("message"))
    except ValueError as exc:
        return _jsonrpc_error(request_id, INVALID_PARAMS, str(exc))

    try:
        result = answer_question(query=query, top_n=DEFAULT_TOP_N, top_k=DEFAULT_TOP_K)
        task_id = _normalize_task_identifier(params.get("id"), "id")
        session_id = _normalize_task_identifier(params.get("sessionId"), "sessionId")
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "result": _completed_task(
                task_id=task_id,
                session_id=session_id,
                answer=result.get("answer", ""),
                request_metadata=params.get("metadata"),
                answer_result=result,
            ),
        }
    except Exception as exc:  # noqa: BLE001 - A2A boundary returns safe error only
        logger.exception("A2A tasks/send failed")
        return _jsonrpc_error(request_id, SERVER_ERROR, answer_exception_detail(exc))


def _is_valid_jsonrpc_envelope(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("jsonrpc") == JSONRPC_VERSION
        and "id" in payload
        and isinstance(payload.get("method"), str)
    )


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _completed_task(
    *,
    task_id: Any,
    session_id: Any,
    answer: Any,
    request_metadata: Any,
    answer_result: dict[str, Any],
) -> dict[str, Any]:
    metadata = _response_metadata(request_metadata, answer_result)
    return {
        "id": task_id,
        "sessionId": session_id,
        "status": {
            "state": "completed",
            "message": {
                "role": "agent",
                "parts": [{"type": "text", "text": str(answer)}],
            },
        },
        "metadata": metadata,
    }


def _response_metadata(
    request_metadata: Any,
    answer_result: dict[str, Any],
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if isinstance(request_metadata, dict):
        metadata.update(
            {
                key: request_metadata[key]
                for key in PASSTHROUGH_METADATA_KEYS
                if key in request_metadata
            }
        )
    metadata["evidence_count"] = answer_result.get("evidence_count", 0)
    metadata["citations"] = answer_result.get("citations", [])
    metadata["retrieval_evidences"] = answer_result.get("retrieval_evidences", [])
    return metadata


def _extract_query(message: Any) -> str:
    if not isinstance(message, dict):
        raise ValueError("A2A message 格式不合法")
    parts = message.get("parts")
    if not isinstance(parts, list):
        raise ValueError("A2A message.parts 格式不合法")
    text_parts = []
    for part in parts:
        if not isinstance(part, dict) or part.get("type") != "text":
            continue
        text = part.get("text")
        if not isinstance(text, str):
            raise ValueError("A2A message.parts[].text 必须是字符串")
        text_parts.append(text)
    query = "\n".join(text_parts).strip()
    if not query:
        raise ValueError("问题不能为空")
    return query


def _normalize_task_identifier(value: Any, field_name: str) -> Any:
    if value is None:
        return str(uuid4())
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"A2A {field_name} 不能为空")
    return value


def _agent_url(request: Request) -> str:
    return f"{str(request.base_url).rstrip('/')}{AGENT_PATH}"
