from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from xhbx_rag.atomic_indexer import AtomicIndexer
from xhbx_rag.config import RetrievalConfig, ingestion_limits_from_env
from xhbx_rag.embedding import EmbeddingClient
from xhbx_rag.milvus_store import create_milvus_store

from .bad_cases import save_bad_case, validate_evidence_feedback_items
from .batch_routes import router as batch_router
from .a2a_routes import router as a2a_router
from .batch_runner import BatchRunner
from .batch_store import BatchRunStore
from .ingestion_routes import cleanup_abandoned_creates, router as ingestion_router
from .ingestion_pipeline import IngestionPipeline
from .ingestion_runner import IngestionRunner
from .ingestion_store import IngestionStore
from .safe_errors import (
    MISSING_CONFIG_ERROR_PREFIX,
    SAFE_ANSWER_ERROR_MESSAGES,
    answer_exception_detail,
    is_safe_answer_error,
)
from .services import (
    LOCAL_INDEX_UNAVAILABLE_ERROR,
    REQUIRED_CONFIG_KEYS,
    answer_question,
    answer_question_stream_events,
    get_status,
)
from .source_paths import SourcePathError, project_root_from_module, reveal_in_finder

logger = logging.getLogger(__name__)

# 兼容旧下划线名，安全错误归一逻辑已迁移至 safe_errors 模块。
_SAFE_ANSWER_ERROR_MESSAGES = SAFE_ANSWER_ERROR_MESSAGES
_MISSING_CONFIG_ERROR_PREFIX = MISSING_CONFIG_ERROR_PREFIX
_is_safe_answer_error = is_safe_answer_error
_answer_exception_detail = answer_exception_detail

SOURCE_REVEAL_CLIENT_ERROR_DETAIL = (
    "无法显示引用文件，请确认文件位于 data 目录内且仍然存在。"
)
BAD_CASE_SAVE_ERROR_DETAIL = "无法保存 bad case"
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
_COLLECTION_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INGESTION_JOBS_ROOT = (
    project_root_from_module() / ".local" / "web_ingestion" / "jobs"
).resolve(strict=False)


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    top_n: int = Field(default=20, ge=1, le=100, strict=True)
    top_k: int = Field(default=5, ge=1, le=20, strict=True)
    collections: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("问题不能为空")
        return value

    @field_validator("collections")
    @classmethod
    def _collections_valid(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            collection = value.strip()
            if (
                not collection
                or len(collection) > 255
                or not _COLLECTION_NAME_RE.fullmatch(collection)
            ):
                raise ValueError("collection 名称不支持")
            if collection not in normalized:
                normalized.append(collection)
        return normalized

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
        return validate_evidence_feedback_items(values)

    @model_validator(mode="after")
    def _top_k_not_greater_than_top_n(self) -> BadCaseRequest:
        if self.top_k > self.top_n:
            raise ValueError("top_k 不能大于 top_n")
        return self


def _build_ingestion_runner(
    store: IngestionStore, limits: Any
) -> IngestionRunner:
    pipeline = IngestionPipeline(limits=limits)

    def indexer_factory(target: str) -> AtomicIndexer:
        if target not in {"case", "course"}:
            raise ValueError("不支持的入库目标")
        config = RetrievalConfig.from_env()
        embedding_client = EmbeddingClient(
            base_url=config.embedding_base_url,
            api_key=config.embedding_api_key,
            model=config.embedding_model_name,
        )
        milvus_store = create_milvus_store(
            config,
            collection_name=config.milvus_collection,
        )
        return AtomicIndexer(
            embedding_client=embedding_client,
            store=milvus_store,
        )

    return IngestionRunner(
        store=store,
        pipeline=pipeline,
        indexer_factory=indexer_factory,
    )


def create_app(
    batch_store: Any | None = None,
    batch_runner: Any | None = None,
    ingestion_store: Any | None = None,
    ingestion_runner: Any | None = None,
) -> FastAPI:
    if ingestion_runner is not None:
        runner_has_store = hasattr(ingestion_runner, "store")
        runner_store = getattr(ingestion_runner, "store", None)
        if ingestion_store is None:
            if not runner_has_store or runner_store is None:
                raise ValueError("仅注入 ingestion_runner 时必须提供 ingestion_runner.store")
            ingestion_store = runner_store
        elif runner_has_store and runner_store is not ingestion_store:
            raise ValueError("ingestion_store 与 ingestion_runner.store 必须是同一个对象")

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
        active_batch_runner = None
        active_ingestion_runner = None
        try:
            store = getattr(app_instance.state, "batch_store", None)
            if store is None:
                store = BatchRunStore()
                app_instance.state.batch_store = store
            batch_runtime = getattr(app_instance.state, "batch_runner", None)
            if batch_runtime is None:
                batch_runtime = BatchRunner(store=store)
                app_instance.state.batch_runner = batch_runtime
            # 先恢复中断状态，再启动 worker，避免旧的 running 状态被并发读到。
            store.recover_after_restart()
            batch_runtime.start()
            active_batch_runner = batch_runtime
        except Exception:
            # 批量子系统初始化失败不应拖垮单问问答；清空 state 让批量路由
            # 一致返回 500「批量任务存储不可用」，其余功能照常启动。
            logger.exception("批量执行子系统初始化失败，Web 将在无批量能力下继续启动")
            app_instance.state.batch_store = None
            app_instance.state.batch_runner = None
            active_batch_runner = None

        try:
            limits = getattr(app_instance.state, "ingestion_limits", None)
            if limits is None:
                limits = ingestion_limits_from_env()
                app_instance.state.ingestion_limits = limits
            ingestion_runtime_store = getattr(
                app_instance.state, "ingestion_store", None
            )
            if ingestion_runtime_store is None:
                ingestion_runtime_store = IngestionStore(
                    jobs_root=_INGESTION_JOBS_ROOT
                )
                app_instance.state.ingestion_store = ingestion_runtime_store
            ingestion_runtime = getattr(app_instance.state, "ingestion_runner", None)
            if ingestion_runtime is None:
                ingestion_runtime = _build_ingestion_runner(
                    ingestion_runtime_store,
                    limits,
                )
                app_instance.state.ingestion_runner = ingestion_runtime
            runner_store = getattr(
                ingestion_runtime, "store", ingestion_runtime_store
            )
            if runner_store is not ingestion_runtime_store:
                raise ValueError("ingestion Store/Runner 运行时绑定不一致")
            # 先清理可识别且 SQLite 明确无主的创建态，再扫描恢复动作；两者
            # 都只访问本地持久化状态，最后才启动可能访问外部服务的 worker。
            cleanup_abandoned_creates(ingestion_runtime_store)
            ingestion_runtime.recover_after_restart()
            ingestion_runtime.start()
            active_ingestion_runner = ingestion_runtime
        except Exception:
            logger.exception("入库子系统初始化失败，Web 将在无入库能力下继续启动")
            app_instance.state.ingestion_store = None
            app_instance.state.ingestion_runner = None
            active_ingestion_runner = None
        try:
            yield
        finally:
            if active_ingestion_runner is not None:
                try:
                    active_ingestion_runner.stop()
                except Exception:
                    logger.exception("入库 Runner 停止失败")
            if active_batch_runner is not None:
                try:
                    active_batch_runner.stop()
                except Exception:
                    logger.exception("批量执行 Runner 停止失败")

    web_app = FastAPI(title="xhbx-rag Web", lifespan=lifespan)
    web_app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )
    # 注入的 store/runner 直接放进 app.state，不依赖 lifespan（便于测试）。
    if batch_store is not None:
        web_app.state.batch_store = batch_store
    if batch_runner is not None:
        web_app.state.batch_runner = batch_runner
    if ingestion_store is not None:
        web_app.state.ingestion_store = ingestion_store
    if ingestion_runner is not None:
        web_app.state.ingestion_runner = ingestion_runner

    @web_app.get("/api/status")
    def status() -> dict[str, Any]:
        return get_status()

    @web_app.post("/api/answer")
    def answer(request: AnswerRequest) -> dict[str, Any]:
        try:
            kwargs: dict[str, Any] = {
                "query": request.query,
                "top_n": request.top_n,
                "top_k": request.top_k,
            }
            if request.collections:
                kwargs["collections"] = request.collections
            return answer_question(**kwargs)
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
            raise HTTPException(
                status_code=502,
                detail=_answer_exception_detail(exc),
            ) from exc

    @web_app.post("/api/answer/stream")
    def answer_stream(request: AnswerRequest) -> StreamingResponse:
        kwargs: dict[str, Any] = {
            "query": request.query,
            "top_n": request.top_n,
            "top_k": request.top_k,
        }
        if request.collections:
            kwargs["collections"] = request.collections
        events = answer_question_stream_events(**kwargs)
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

    web_app.include_router(a2a_router)
    web_app.include_router(batch_router)
    web_app.include_router(ingestion_router)

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
