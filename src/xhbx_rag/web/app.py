from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .bad_cases import save_bad_case
from .services import (
    LOCAL_INDEX_UNAVAILABLE_ERROR,
    REQUIRED_CONFIG_KEYS,
    answer_question,
    answer_question_stream_events,
    get_status,
)
from .source_paths import SourcePathError, reveal_in_finder

logger = logging.getLogger(__name__)

_SAFE_ANSWER_ERROR_MESSAGES = {
    "问题不能为空",
    "top_n 必须在 1 到 100 之间",
    "top_k 必须在 1 到 20 之间",
    "top_k 不能大于 top_n",
    "配置解析失败，请检查 .env 中的数值配置。",
}

SOURCE_REVEAL_CLIENT_ERROR_DETAIL = (
    "无法显示引用文件，请确认文件位于 data 目录内且仍然存在。"
)
BAD_CASE_SAVE_ERROR_DETAIL = "无法保存 bad case"
_MISSING_CONFIG_ERROR_PREFIX = "缺少必要环境变量:"
_SAFE_CONFIG_KEYS = set(REQUIRED_CONFIG_KEYS)
_ALLOWED_BAD_CASE_ISSUE_TYPES = {
    "usable",
    "inaccurate",
    "incomplete",
    "citation_issue",
    "customer_mismatch",
    "off_topic",
    "missing_talk_track",
    "case_mismatch",
    "citation_mismatch",
    "not_customer_ready",
    "compliance_risk",
    "missing_knowledge",
    "ranking_wrong",
    "citation_wrong",
    "answer_unsupported",
    "other",
}
_ALLOWED_BAD_CASE_FEEDBACK_RESULTS = {
    "usable",
    "inaccurate",
    "incomplete",
    "citation_issue",
    "customer_mismatch",
}
_ALLOWED_BAD_CASE_PROBLEM_TAGS = {
    "off_topic",
    "missing_talk_track",
    "case_mismatch",
    "citation_mismatch",
    "not_customer_ready",
    "compliance_risk",
    "other",
}
_ALLOWED_EVIDENCE_FEEDBACK_JUDGEMENTS = {"should_use", "should_not_use"}


def _is_safe_answer_error(message: str) -> bool:
    if message in _SAFE_ANSWER_ERROR_MESSAGES:
        return True
    if not message.startswith(_MISSING_CONFIG_ERROR_PREFIX):
        return False

    raw_keys = message.removeprefix(_MISSING_CONFIG_ERROR_PREFIX)
    keys = [item.strip() for item in raw_keys.split(",")]
    return bool(keys) and all(key in _SAFE_CONFIG_KEYS for key in keys)


def _answer_exception_detail(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        message = str(exc)
        if message == LOCAL_INDEX_UNAVAILABLE_ERROR:
            return message
        if _is_safe_answer_error(message):
            return message
    return "问答服务暂时不可用"


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    top_n: int = Field(default=20, ge=1, le=100, strict=True)
    top_k: int = Field(default=5, ge=1, le=20, strict=True)

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("问题不能为空")
        return value

    @model_validator(mode="after")
    def _top_k_not_greater_than_top_n(self) -> AnswerRequest:
        if self.top_k > self.top_n:
            raise ValueError("top_k 不能大于 top_n")
        return self


class RevealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(min_length=1)


class BadCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    rewritten_query: str = Field(default="", max_length=2000)
    answer: str = Field(min_length=1, max_length=20000)
    top_n: int = Field(ge=1, le=100, strict=True)
    top_k: int = Field(ge=1, le=20, strict=True)
    feedback_result: str = Field(default="", max_length=64)
    problem_tags: list[str] = Field(default_factory=list, max_length=10)
    problem_detail: str = Field(default="", max_length=8000)
    expected_answer: str = Field(default="", max_length=8000)
    reference_note: str = Field(default="", max_length=2000)
    evidence_feedback: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    issue_types: list[str] = Field(min_length=1, max_length=12)
    expected_knowledge: str = Field(default="", max_length=8000)
    expected_source: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=8000)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_evidences: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("query", "answer")
    @classmethod
    def _required_text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("字段不能为空")
        return value

    @field_validator("issue_types")
    @classmethod
    def _issue_types_allowed(cls, values: list[str]) -> list[str]:
        if any(value not in _ALLOWED_BAD_CASE_ISSUE_TYPES for value in values):
            raise ValueError("bad case 类型不支持")
        return values

    @field_validator("feedback_result")
    @classmethod
    def _feedback_result_allowed(cls, value: str) -> str:
        if value and value not in _ALLOWED_BAD_CASE_FEEDBACK_RESULTS:
            raise ValueError("反馈结果不支持")
        return value

    @field_validator("problem_tags")
    @classmethod
    def _problem_tags_allowed(cls, values: list[str]) -> list[str]:
        if any(value not in _ALLOWED_BAD_CASE_PROBLEM_TAGS for value in values):
            raise ValueError("反馈问题点不支持")
        return values

    @field_validator("evidence_feedback")
    @classmethod
    def _evidence_feedback_allowed(
        cls, values: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        for value in values:
            judgement = value.get("judgement")
            if judgement not in _ALLOWED_EVIDENCE_FEEDBACK_JUDGEMENTS:
                raise ValueError("证据反馈类型不支持")
        return values

    @model_validator(mode="after")
    def _top_k_not_greater_than_top_n(self) -> BadCaseRequest:
        if self.top_k > self.top_n:
            raise ValueError("top_k 不能大于 top_n")
        return self


def create_app() -> FastAPI:
    web_app = FastAPI(title="xhbx-rag Web")
    web_app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @web_app.get("/api/status")
    def status() -> dict[str, Any]:
        return get_status()

    @web_app.post("/api/answer")
    def answer(request: AnswerRequest) -> dict[str, Any]:
        try:
            return answer_question(
                query=request.query,
                top_n=request.top_n,
                top_k=request.top_k,
            )
        except ValueError as exc:
            message = str(exc)
            if message == LOCAL_INDEX_UNAVAILABLE_ERROR:
                raise HTTPException(status_code=503, detail=message) from exc
            if _is_safe_answer_error(message):
                raise HTTPException(status_code=400, detail=message) from exc
            logger.exception("Answer route failed")
            raise HTTPException(status_code=502, detail="问答服务暂时不可用") from exc
        except Exception as exc:  # noqa: BLE001 - API boundary returns safe summary
            logger.exception("Answer route failed")
            raise HTTPException(status_code=502, detail="问答服务暂时不可用") from exc

    @web_app.post("/api/answer/stream")
    def answer_stream(request: AnswerRequest) -> StreamingResponse:
        events = answer_question_stream_events(
            query=request.query,
            top_n=request.top_n,
            top_k=request.top_k,
        )
        return StreamingResponse(
            _sse_answer_events(events),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @web_app.post("/api/source/reveal")
    def reveal(request: RevealRequest) -> dict[str, Any]:
        try:
            resolved_path = reveal_in_finder(request.source_path)
        except SourcePathError as exc:
            logger.exception("Source reveal rejected")
            raise HTTPException(
                status_code=400,
                detail=SOURCE_REVEAL_CLIENT_ERROR_DETAIL,
            ) from exc
        except Exception as exc:  # noqa: BLE001 - OS reveal failures are reported safely
            logger.exception("Reveal source route failed")
            raise HTTPException(status_code=500, detail="无法在 Finder 中显示文件") from exc
        return {"ok": True, "resolved_path": str(resolved_path)}

    @web_app.post("/api/bad-cases")
    def bad_case(request: BadCaseRequest) -> dict[str, Any]:
        try:
            result = save_bad_case(request.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001 - API boundary returns safe summary
            logger.exception("Bad case route failed")
            raise HTTPException(status_code=500, detail=BAD_CASE_SAVE_ERROR_DETAIL) from exc
        return {"ok": True, "bad_case_id": result["bad_case_id"]}

    return web_app


def _sse_answer_events(events: Any) -> Any:
    for event in events:
        if isinstance(event, dict) and event.get("type") == "_exception":
            exc = event.get("exception")
            if isinstance(exc, Exception):
                _log_answer_stream_exception(exc)
                yield _sse_event(
                    "error",
                    {"type": "error", "detail": _answer_exception_detail(exc)},
                )
            else:
                yield _sse_event(
                    "error",
                    {"type": "error", "detail": "问答服务暂时不可用"},
                )
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "message")
        yield _sse_event(event_type, event)


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
    )


def _log_answer_stream_exception(exc: Exception) -> None:
    try:
        raise exc
    except Exception:  # noqa: BLE001 - re-raise only to attach traceback to log
        logger.exception("Answer stream route failed")


app = create_app()
