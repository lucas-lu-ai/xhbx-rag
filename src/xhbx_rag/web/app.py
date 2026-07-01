from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .services import REQUIRED_CONFIG_KEYS, answer_question, get_status
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
_MISSING_CONFIG_ERROR_PREFIX = "缺少必要环境变量:"
_SAFE_CONFIG_KEYS = set(REQUIRED_CONFIG_KEYS)


def _is_safe_answer_error(message: str) -> bool:
    if message in _SAFE_ANSWER_ERROR_MESSAGES:
        return True
    if not message.startswith(_MISSING_CONFIG_ERROR_PREFIX):
        return False

    raw_keys = message.removeprefix(_MISSING_CONFIG_ERROR_PREFIX)
    keys = [item.strip() for item in raw_keys.split(",")]
    return bool(keys) and all(key in _SAFE_CONFIG_KEYS for key in keys)


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
            if _is_safe_answer_error(message):
                raise HTTPException(status_code=400, detail=message) from exc
            logger.exception("Answer route failed")
            raise HTTPException(status_code=502, detail="问答服务暂时不可用") from exc
        except Exception as exc:  # noqa: BLE001 - API boundary returns safe summary
            logger.exception("Answer route failed")
            raise HTTPException(status_code=502, detail="问答服务暂时不可用") from exc

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

    return web_app


app = create_app()
