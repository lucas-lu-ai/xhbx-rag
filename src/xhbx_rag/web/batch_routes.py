"""批量执行会话的 FastAPI 路由。

store / runner 从 request.app.state 获取；任一缺失或 SQLite 故障时
统一返回 500「批量任务存储不可用」，错误 detail 全部为固定中文常量。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Annotated, Any, Callable, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .bad_cases import (
    ISSUE_TYPE_LABELS,
    save_bad_case,
    validate_evidence_feedback_items,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ------------------------------------------------------------------ 错误文案
RUN_NOT_FOUND_DETAIL = "批量会话不存在"
ROW_NOT_FOUND_DETAIL = "批量行不存在"
DELETE_RUNNING_CONFLICT_DETAIL = "批量任务正在执行，无法删除"
RETRY_CONFLICT_DETAIL = "当前状态不允许重试"
RESUME_CONFLICT_DETAIL = "当前状态不允许继续执行"
BAD_CASE_CONFLICT_DETAIL = "当前状态不允许保存反馈"
STORE_UNAVAILABLE_DETAIL = "批量任务存储不可用"
BAD_CASE_SAVE_ERROR_DETAIL = "无法保存 bad case"

# ------------------------------------------------------------------ 校验上限
MAX_TITLE_LENGTH = 200
MAX_SOURCE_LABEL_LENGTH = 200
MAX_HEADERS = 50
MAX_HEADER_LENGTH = 200
MAX_ROWS = 1000
MAX_COLUMNS = 50
MAX_CELL_LENGTH = 20000
MAX_QUESTIONS = 100
MAX_QUERY_LENGTH = 2000
MAX_INPUT_ANSWER_LENGTH = 20000

# bad case 枚举与单问接口保持一致；大集合复用 bad_cases 的标签表作为唯一来源。
_ALLOWED_BAD_CASE_ISSUE_TYPES = frozenset(ISSUE_TYPE_LABELS)
_ALLOWED_BAD_CASE_FEEDBACK_RESULTS = frozenset(
    {"usable", "inaccurate", "incomplete", "citation_issue", "customer_mismatch"}
)
_ALLOWED_BAD_CASE_PROBLEM_TAGS = frozenset(
    {
        "off_topic",
        "missing_talk_track",
        "case_mismatch",
        "citation_mismatch",
        "not_customer_ready",
        "compliance_risk",
        "other",
    }
)


# ------------------------------------------------------------------ 请求模型


class BatchQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_index: int = Field(ge=1, strict=True)
    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    input_answer: str = Field(default="", max_length=MAX_INPUT_ANSWER_LENGTH)
    top_n: int = Field(default=20, ge=1, le=100, strict=True)
    top_k: int = Field(default=5, ge=1, le=20, strict=True)

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("问题不能为空")
        return value

    @model_validator(mode="after")
    def _top_k_not_greater_than_top_n(self) -> BatchQuestionRequest:
        if self.top_k > self.top_n:
            raise ValueError("top_k 不能大于 top_n")
        return self


class BatchRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=MAX_TITLE_LENGTH)
    source_label: str = Field(min_length=1, max_length=MAX_SOURCE_LABEL_LENGTH)
    source_format: Literal["txt", "csv", "xlsx", "pasted"]
    headers: list[str] = Field(max_length=MAX_HEADERS)
    rows: list[list[str]] = Field(max_length=MAX_ROWS)
    questions: list[BatchQuestionRequest] = Field(
        min_length=1, max_length=MAX_QUESTIONS
    )

    @field_validator("source_label")
    @classmethod
    def _source_label_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("来源名称不能为空")
        return value

    @field_validator("headers")
    @classmethod
    def _headers_length_allowed(cls, values: list[str]) -> list[str]:
        if any(len(value) > MAX_HEADER_LENGTH for value in values):
            raise ValueError("表头长度超出限制")
        return values

    @field_validator("rows")
    @classmethod
    def _rows_shape_allowed(cls, values: list[list[str]]) -> list[list[str]]:
        for row in values:
            if len(row) > MAX_COLUMNS:
                raise ValueError("表格列数超出限制")
            if any(len(cell) > MAX_CELL_LENGTH for cell in row):
                raise ValueError("单元格长度超出限制")
        return values

    @model_validator(mode="after")
    def _questions_reference_valid_rows(self) -> BatchRunCreateRequest:
        row_indexes = [question.row_index for question in self.questions]
        if len(set(row_indexes)) != len(row_indexes):
            raise ValueError("row_index 不能重复")
        if any(row_index > len(self.rows) for row_index in row_indexes):
            raise ValueError("row_index 超出表格行数")
        return self


class BatchBadCaseRequest(BaseModel):
    """批量行反馈请求：单问 BadCaseRequest 全部字段 + 批量补充字段。"""

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
    citations: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    retrieval_evidences: list[dict[str, Any]] = Field(
        default_factory=list, max_length=200
    )
    input_answer: str = Field(default="", max_length=MAX_INPUT_ANSWER_LENGTH)
    batch_source_label: str = Field(min_length=1, max_length=MAX_SOURCE_LABEL_LENGTH)

    @field_validator("query", "answer", "batch_source_label")
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
    def _top_k_not_greater_than_top_n(self) -> BatchBadCaseRequest:
        if self.top_k > self.top_n:
            raise ValueError("top_k 不能大于 top_n")
        return self


# ------------------------------------------------------------------ 依赖获取


def _get_store(request: Request) -> Any:
    store = getattr(request.app.state, "batch_store", None)
    if store is None:
        raise HTTPException(status_code=500, detail=STORE_UNAVAILABLE_DETAIL)
    return store


def _get_runner(request: Request) -> Any:
    runner = getattr(request.app.state, "batch_runner", None)
    if runner is None:
        raise HTTPException(status_code=500, detail=STORE_UNAVAILABLE_DETAIL)
    return runner


def _run_store_operation(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except sqlite3.Error as exc:
        logger.exception("批量任务存储访问失败")
        raise HTTPException(status_code=500, detail=STORE_UNAVAILABLE_DETAIL) from exc


def _raise_for_result(result: str, *, conflict_detail: str) -> None:
    if result == "run_not_found":
        raise HTTPException(status_code=404, detail=RUN_NOT_FOUND_DETAIL)
    if result == "row_not_found":
        raise HTTPException(status_code=404, detail=ROW_NOT_FOUND_DETAIL)
    if result == "conflict":
        raise HTTPException(status_code=409, detail=conflict_detail)


RowIndexPath = Annotated[int, PathParam(ge=1)]


# ------------------------------------------------------------------ 路由


@router.post("/api/batch-runs", status_code=201)
def create_batch_run(payload: BatchRunCreateRequest, request: Request) -> dict[str, Any]:
    store = _get_store(request)
    runner = _get_runner(request)
    entry = _run_store_operation(
        lambda: store.create_run(
            title=payload.title,
            source_label=payload.source_label,
            source_format=payload.source_format,
            headers=payload.headers,
            rows=payload.rows,
            questions=[question.model_dump() for question in payload.questions],
        )
    )
    # 先 COMMIT（create_run 返回即已提交）再入队。
    runner.enqueue(entry["run_id"])
    return entry


@router.get("/api/batch-runs")
def list_batch_runs(request: Request) -> dict[str, Any]:
    store = _get_store(request)
    runs = _run_store_operation(lambda: store.list_runs())
    return {"runs": runs}


@router.get("/api/batch-runs/{run_id}/progress")
def batch_run_progress(run_id: str, request: Request) -> dict[str, Any]:
    store = _get_store(request)
    progress = _run_store_operation(lambda: store.get_progress(run_id))
    if progress is None:
        raise HTTPException(status_code=404, detail=RUN_NOT_FOUND_DETAIL)
    return progress


@router.get("/api/batch-runs/{run_id}")
def get_batch_run(
    run_id: str,
    request: Request,
    include_table: bool = False,
) -> dict[str, Any]:
    store = _get_store(request)
    run = _run_store_operation(
        lambda: store.get_run(run_id, include_table=include_table)
    )
    if run is None:
        raise HTTPException(status_code=404, detail=RUN_NOT_FOUND_DETAIL)
    return run


@router.post("/api/batch-runs/{run_id}/rows/{row_index}/retry")
def retry_batch_row(
    run_id: str,
    row_index: RowIndexPath,
    request: Request,
) -> dict[str, Any]:
    store = _get_store(request)
    runner = _get_runner(request)
    result = _run_store_operation(lambda: store.retry_row(run_id, row_index))
    _raise_for_result(result, conflict_detail=RETRY_CONFLICT_DETAIL)
    runner.enqueue(run_id)
    return {"ok": True}


@router.post("/api/batch-runs/{run_id}/resume")
def resume_batch_run(run_id: str, request: Request) -> dict[str, Any]:
    store = _get_store(request)
    runner = _get_runner(request)
    result = _run_store_operation(lambda: store.resume_run(run_id))
    _raise_for_result(result, conflict_detail=RESUME_CONFLICT_DETAIL)
    runner.enqueue(run_id)
    return {"ok": True}


@router.post("/api/batch-runs/{run_id}/rows/{row_index}/bad-case")
def save_batch_row_bad_case(
    run_id: str,
    row_index: RowIndexPath,
    payload: BatchBadCaseRequest,
    request: Request,
) -> dict[str, Any]:
    store = _get_store(request)
    bad_case_id = f"bad-{uuid4().hex}"
    record = payload.model_dump(mode="json")
    record["run_id"] = run_id
    record["row_index"] = row_index
    bad_case_record = {**record, "bad_case_id": bad_case_id}

    # 先写 SQLite（事务内完成存在性 + 终态校验），成功后再向唯一评测数据源
    # JSONL 追加：任一侧失败都不会在 JSONL 里留下孤儿/重复记录。
    result = _run_store_operation(
        lambda: store.save_row_bad_case(
            run_id,
            row_index,
            json.dumps(bad_case_record, ensure_ascii=False, default=str),
        )
    )
    _raise_for_result(result, conflict_detail=BAD_CASE_CONFLICT_DETAIL)

    try:
        save_bad_case(record, bad_case_id=bad_case_id)
    except Exception as exc:  # noqa: BLE001 - API 边界只返回安全文案
        logger.exception("批量行 bad case 落盘失败")
        # 补偿清空缓存字段，避免 SQLite 残留未进入评测数据源的记录。
        try:
            store.clear_row_bad_case(run_id, row_index)
        except Exception:  # noqa: BLE001 - best-effort 补偿
            logger.exception("批量行 bad case 缓存补偿清理失败")
        raise HTTPException(
            status_code=500, detail=BAD_CASE_SAVE_ERROR_DETAIL
        ) from exc

    return {"ok": True, "bad_case_id": bad_case_id}


@router.delete("/api/batch-runs/{run_id}")
def delete_batch_run(run_id: str, request: Request) -> dict[str, Any]:
    store = _get_store(request)
    result = _run_store_operation(lambda: store.delete_run(run_id))
    _raise_for_result(result, conflict_detail=DELETE_RUNNING_CONFLICT_DETAIL)
    return {"ok": True}
