from __future__ import annotations

import logging
import os
import re
import sqlite3
import unicodedata
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status

from xhbx_rag.web.ingestion_store import IngestionStore
from xhbx_rag.web.ingestion_uploads import (
    IngestionLimits,
    UploadValidationError,
    clear_attempt_workspaces,
    delete_job_workspace,
    preflight_upload,
    save_upload_file,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ingestion-jobs", tags=["ingestion"])

_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_NOT_FOUND_DETAIL = "入库任务不存在"
_CONFLICT_DETAIL = "当前任务状态不允许此操作"
_UPLOAD_INVALID_DETAIL = "上传文件无效"
_UPLOAD_TOO_LARGE_DETAIL = "上传文件超过大小限制"
_STORAGE_UNAVAILABLE_DETAIL = "任务存储暂时不可用"


def _store(request: Request) -> IngestionStore:
    store = getattr(request.app.state, "ingestion_store", None)
    if store is None:
        raise HTTPException(status_code=500, detail=_STORAGE_UNAVAILABLE_DETAIL)
    return store


def _runner(request: Request) -> Any:
    runner = getattr(request.app.state, "ingestion_runner", None)
    if runner is None:
        raise HTTPException(status_code=500, detail=_STORAGE_UNAVAILABLE_DETAIL)
    return runner


def _limits(request: Request) -> IngestionLimits:
    limits = getattr(request.app.state, "ingestion_limits", None)
    return limits if isinstance(limits, IngestionLimits) else IngestionLimits()


def _validated_job_id(job_id: str) -> str:
    if not _JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
    return job_id


def _safe_original_name(filename: str | None) -> str:
    raw = unicodedata.normalize("NFC", filename or "")
    basename = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    basename = basename.lstrip(".")
    safe = "".join(
        character
        if character.isalnum() or character in {".", "_", "-", " ", "(", ")"}
        else "_"
        for character in basename
    ).strip(" .")
    if not safe:
        safe = "upload"
    suffix = Path(safe).suffix[:32]
    stem_limit = max(1, 180 - len(suffix))
    stem = Path(safe).stem[:stem_limit].strip(" .") or "upload"
    return f"{stem}{suffix}"


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_source_layout(store: IngestionStore, job_id: str) -> tuple[Path, Path]:
    jobs_root = store.jobs_root
    jobs_root.mkdir(parents=True, exist_ok=True)
    if jobs_root.is_symlink() or not jobs_root.is_dir():
        raise OSError("jobs_root 不是可信目录")
    job_dir = jobs_root / job_id
    job_dir.mkdir(mode=0o700)
    source_dir = job_dir / "source"
    source_dir.mkdir(mode=0o700)
    _fsync_directory(jobs_root)
    _fsync_directory(job_dir)
    return job_dir, source_dir


def _discard_failed_draft(store: IngestionStore, job_id: str) -> None:
    try:
        if store.get_job(job_id) is not None:
            result = store.begin_delete(job_id)
            if result == "ok":
                store.finish_delete(job_id)
    except Exception:
        logger.exception("清理失败 draft 数据库记录失败 job_id=%s", job_id)


def _cleanup_create_failure(
    store: IngestionStore, job_id: str, job_dir: Path, *, owns_job_dir: bool
) -> None:
    _discard_failed_draft(store, job_id)
    if owns_job_dir:
        try:
            delete_job_workspace(job_dir)
            if store.jobs_root.is_dir():
                _fsync_directory(store.jobs_root)
        except Exception:
            logger.exception("清理失败上传目录失败 job_id=%s", job_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_ingestion_job(
    request: Request,
    file: Annotated[UploadFile, File()],
    target: Annotated[Literal["case", "course"], Form()],
) -> dict[str, Any]:
    store = _store(request)
    limits = _limits(request)
    job_id = uuid.uuid4().hex
    if not _JOB_ID_RE.fullmatch(job_id):
        logger.error("uuid4 生成了无效 ingestion job_id")
        raise HTTPException(status_code=500, detail=_STORAGE_UNAVAILABLE_DETAIL)
    job_dir = store.jobs_root / job_id
    owns_job_dir = False
    try:
        job_dir, source_dir = _create_source_layout(store, job_id)
        owns_job_dir = True
        source_path = source_dir / _safe_original_name(file.filename)
        try:
            await save_upload_file(
                file,
                source_path,
                max_bytes=limits.max_upload_bytes,
            )
        except UploadValidationError as exc:
            _cleanup_create_failure(
                store, job_id, job_dir, owns_job_dir=owns_job_dir
            )
            if str(exc) == _UPLOAD_TOO_LARGE_DETAIL:
                raise HTTPException(status_code=413, detail=_UPLOAD_TOO_LARGE_DETAIL) from exc
            raise HTTPException(status_code=400, detail=_UPLOAD_INVALID_DETAIL) from exc
        _fsync_directory(source_dir)
        try:
            preflight = preflight_upload(source_path, target=target, limits=limits)
        except UploadValidationError as exc:
            _cleanup_create_failure(
                store, job_id, job_dir, owns_job_dir=owns_job_dir
            )
            raise HTTPException(status_code=400, detail=_UPLOAD_INVALID_DETAIL) from exc
        return store.create_draft(
            preflight=preflight,
            source_path=source_path,
            job_id=job_id,
        )
    except HTTPException:
        raise
    except (sqlite3.Error, OSError, ValueError) as exc:
        _cleanup_create_failure(store, job_id, job_dir, owns_job_dir=owns_job_dir)
        logger.exception("创建入库任务失败 job_id=%s", job_id)
        raise HTTPException(status_code=500, detail=_STORAGE_UNAVAILABLE_DETAIL) from exc
    except Exception as exc:
        _cleanup_create_failure(store, job_id, job_dir, owns_job_dir=owns_job_dir)
        logger.exception("创建入库任务失败 job_id=%s", job_id)
        raise HTTPException(status_code=500, detail=_STORAGE_UNAVAILABLE_DETAIL) from exc
    finally:
        await file.close()


@router.post("/{job_id}/start")
def start_ingestion_job(job_id: str, request: Request) -> dict[str, Any]:
    job_id = _validated_job_id(job_id)
    store = _store(request)
    runner = _runner(request)
    try:
        if store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
        if store.start_job(job_id) != "ok":
            raise HTTPException(status_code=409, detail=_CONFLICT_DETAIL)
        runner.enqueue(job_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("启动入库任务失败 job_id=%s", job_id)
        raise HTTPException(status_code=500, detail=_STORAGE_UNAVAILABLE_DETAIL) from exc
    return {"ok": True, "job_id": job_id, "status": "queued"}


@router.get("")
def list_ingestion_jobs(request: Request) -> dict[str, Any]:
    try:
        return {"jobs": _store(request).list_jobs(limit=200)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("查询入库任务列表失败")
        raise HTTPException(status_code=500, detail=_STORAGE_UNAVAILABLE_DETAIL) from exc


@router.get("/{job_id}")
def get_ingestion_job(job_id: str, request: Request) -> dict[str, Any]:
    job_id = _validated_job_id(job_id)
    try:
        job = _store(request).get_job(job_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("查询入库任务失败 job_id=%s", job_id)
        raise HTTPException(status_code=500, detail=_STORAGE_UNAVAILABLE_DETAIL) from exc
    if job is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
    return job


@router.get("/{job_id}/progress")
def get_ingestion_progress(job_id: str, request: Request) -> dict[str, Any]:
    job_id = _validated_job_id(job_id)
    try:
        progress = _store(request).get_progress(job_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("查询入库任务进度失败 job_id=%s", job_id)
        raise HTTPException(status_code=500, detail=_STORAGE_UNAVAILABLE_DETAIL) from exc
    if progress is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
    return progress


@router.post("/{job_id}/retry")
def retry_ingestion_job(job_id: str, request: Request) -> dict[str, Any]:
    job_id = _validated_job_id(job_id)
    store = _store(request)
    runner = _runner(request)
    try:
        if store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
        reserved = store.retry_job(job_id)
        if reserved.get("result") != "ok":
            raise HTTPException(status_code=409, detail=_CONFLICT_DETAIL)
        attempt_no = int(reserved["attempt_no"])
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("预留入库重试失败 job_id=%s", job_id)
        raise HTTPException(status_code=500, detail=_STORAGE_UNAVAILABLE_DETAIL) from exc

    try:
        clear_attempt_workspaces(store.jobs_root / job_id)
    except Exception as cleanup_exc:
        try:
            store.abort_retry(job_id, attempt_no, "无法清理旧任务产物")
        except Exception:
            logger.exception(
                "中止清理失败的入库重试失败 job_id=%s attempt_no=%s",
                job_id,
                attempt_no,
            )
        logger.exception(
            "清理入库重试工作区失败 job_id=%s attempt_no=%s", job_id, attempt_no
        )
        raise HTTPException(status_code=500, detail=_STORAGE_UNAVAILABLE_DETAIL) from cleanup_exc

    try:
        runner.enqueue(job_id)
    except Exception as exc:
        logger.exception("入库重试入队失败 job_id=%s attempt_no=%s", job_id, attempt_no)
        raise HTTPException(status_code=500, detail=_STORAGE_UNAVAILABLE_DETAIL) from exc
    return {
        "ok": True,
        "job_id": job_id,
        "attempt_no": attempt_no,
        "status": "queued",
    }


@router.delete("/{job_id}")
def delete_ingestion_job(job_id: str, request: Request) -> dict[str, Any]:
    job_id = _validated_job_id(job_id)
    store = _store(request)
    try:
        result = store.begin_delete(job_id)
        if result == "not_found":
            raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
        if result == "conflict":
            raise HTTPException(status_code=409, detail=_CONFLICT_DETAIL)
        delete_job_workspace(store.jobs_root / job_id)
        store.finish_delete(job_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("删除入库任务失败 job_id=%s", job_id)
        raise HTTPException(status_code=500, detail=_STORAGE_UNAVAILABLE_DETAIL) from exc
    return {"ok": True, "job_id": job_id, "status": "deleted"}
