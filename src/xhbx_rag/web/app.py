from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from .services import answer_question, get_status
from .source_paths import SourcePathError, reveal_in_finder


class AnswerRequest(BaseModel):
    query: str = Field(min_length=1)
    top_n: int = Field(default=20, ge=1, le=100)
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("问题不能为空")
        return value


class RevealRequest(BaseModel):
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
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - API boundary returns safe summary
            raise HTTPException(status_code=502, detail=f"问答失败: {exc}") from exc

    @web_app.post("/api/source/reveal")
    def reveal(request: RevealRequest) -> dict[str, Any]:
        try:
            resolved_path = reveal_in_finder(request.source_path)
        except (SourcePathError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - OS reveal failures are reported safely
            raise HTTPException(status_code=500, detail=f"无法在 Finder 中显示文件: {exc}") from exc
        return {"ok": True, "resolved_path": str(resolved_path)}

    return web_app


app = create_app()
