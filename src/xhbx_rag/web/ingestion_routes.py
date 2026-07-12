from __future__ import annotations

import asyncio
import logging
import os
import re
import stat
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
_CREATING_PREFIX = ".creating-"
_CREATING_MARKER = ".creating"


class _ActualUploadTooLarge(UploadValidationError):
    pass


def _exception_info(exc: BaseException) -> tuple[type[BaseException], BaseException, object]:
    return type(exc), exc, exc.__traceback__


def _store(request: Request) -> IngestionStore:
    store = getattr(request.app.state, "ingestion_store", None)
    if store is None:
        raise HTTPException(status_code=500, detail=_STORAGE_UNAVAILABLE_DETAIL)
    runner = getattr(request.app.state, "ingestion_runner", None)
    runner_store = getattr(runner, "store", store)
    if runner_store is not store:
        logger.error("ingestion Store/Runner 注入不一致")
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
    job_dir = jobs_root / f"{_CREATING_PREFIX}{job_id}"
    job_dir.mkdir(mode=0o700)
    marker = job_dir / _CREATING_MARKER
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(marker, flags, 0o600)
    try:
        os.write(descriptor, job_id.encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    source_dir = job_dir / "source"
    source_dir.mkdir(mode=0o700)
    _fsync_directory(jobs_root)
    _fsync_directory(job_dir)
    return job_dir, source_dir


def _publish_source_layout(
    store: IngestionStore, job_id: str, creating_dir: Path, source_name: str
) -> tuple[Path, Path]:
    final_dir = store.jobs_root / job_id
    if final_dir.exists() or final_dir.is_symlink():
        raise FileExistsError("ingestion job workspace 已存在")
    creating_dir.replace(final_dir)
    return final_dir, final_dir / "source" / source_name


def _rollback_failed_create(
    store: IngestionStore,
    job_id: str,
    *,
    temp_dir: Path,
    final_dir: Path,
    owns_temp_dir: bool,
    published_final_dir: bool,
    draft_created: bool,
) -> None:
    if draft_created:
        try:
            delete_result = store.begin_delete(job_id)
        except Exception:
            logger.exception("预留失败 draft 删除状态失败 job_id=%s", job_id)
            return
        if delete_result != "ok":
            logger.error(
                "失败 draft 所有权不明确，保留现场 job_id=%s result=%s",
                job_id,
                delete_result,
            )
            return
        try:
            delete_job_workspace(final_dir)
            if store.jobs_root.is_dir():
                _fsync_directory(store.jobs_root)
        except Exception:
            logger.exception("清理失败 draft 目录失败 job_id=%s", job_id)
            return
        try:
            store.finish_delete(job_id)
        except Exception:
            logger.exception("完成失败 draft 删除失败 job_id=%s", job_id)
        return

    if published_final_dir:
        logger.error("create_draft 结果不确定，保留 final marker job_id=%s", job_id)
        return

    if owns_temp_dir:
        try:
            delete_job_workspace(temp_dir)
            if store.jobs_root.is_dir():
                _fsync_directory(store.jobs_root)
        except Exception:
            logger.exception("清理失败 temp 上传目录失败 job_id=%s", job_id)


def cleanup_abandoned_creates(store: object) -> None:
    jobs_root = getattr(store, "jobs_root", None)
    if not isinstance(jobs_root, Path) or not jobs_root.exists():
        return
    if jobs_root.is_symlink() or not jobs_root.is_dir():
        logger.error("跳过不可信 ingestion jobs_root 创建态扫描")
        return
    try:
        candidates = list(jobs_root.iterdir())
    except OSError:
        logger.exception("扫描 ingestion 创建态目录失败")
        return
    for candidate in candidates:
        name = candidate.name
        if name.startswith(_CREATING_PREFIX):
            job_id = name[len(_CREATING_PREFIX) :]
        elif _JOB_ID_RE.fullmatch(name):
            job_id = name
        else:
            continue
        if not _JOB_ID_RE.fullmatch(job_id):
            continue
        try:
            metadata = candidate.lstat()
        except OSError:
            continue
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            continue
        try:
            existing = store.get_job(job_id)  # type: ignore[attr-defined]
        except Exception:
            logger.exception("查询 ingestion 创建态所有权失败 job_id=%s", job_id)
            continue
        if existing is not None:
            continue
        marker = candidate / _CREATING_MARKER
        try:
            marker_metadata = marker.lstat()
        except FileNotFoundError:
            try:
                if any(candidate.iterdir()):
                    continue
            except OSError:
                continue
        except OSError:
            continue
        else:
            if (
                not stat.S_ISREG(marker_metadata.st_mode)
                or stat.S_ISLNK(marker_metadata.st_mode)
                or marker_metadata.st_size != len(job_id)
            ):
                continue
            try:
                marker_content = marker.read_bytes()
            except OSError:
                continue
            if marker_content != job_id.encode("ascii"):
                continue
        try:
            delete_job_workspace(candidate)
            _fsync_directory(jobs_root)
        except Exception:
            logger.exception("清理遗留 ingestion 创建态失败 job_id=%s", job_id)


async def _close_uploads(uploads: list[object]) -> BaseException | None:
    # Starlette/FastAPI teardown 可能在 handler 之后再次 close；UploadFile.close
    # 按接口是幂等的，因此这里负责完成本次 close，但不依赖“只调用一次”。
    first_error: BaseException | None = None
    seen: set[int] = set()

    def remember(exc: BaseException) -> None:
        nonlocal first_error
        if first_error is None or (
            not isinstance(exc, Exception)
            and isinstance(first_error, Exception)
        ):
            first_error = exc
        else:
            logger.error("关闭附加上传文件失败", exc_info=_exception_info(exc))

    for upload in uploads:
        if id(upload) in seen:
            continue
        seen.add(id(upload))
        close = getattr(upload, "close", None)
        if not callable(close):
            continue
        try:
            close_task = asyncio.ensure_future(close())
        except BaseException as exc:
            remember(exc)
            continue
        while True:
            try:
                await asyncio.shield(close_task)
                break
            except asyncio.CancelledError as exc:
                remember(exc)
                if close_task.done():
                    break
                # 外层取消不得遗留仍在运行的 UploadFile.close；推迟重抛，
                # 继续 shield 到 close 真正完成，再做同步数据库/目录回滚。
                continue
            except BaseException as exc:
                remember(exc)
                break
    return first_error


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_ingestion_job(
    request: Request,
    file: Annotated[UploadFile, File()],
    target: Annotated[Literal["case", "course"], Form()],
) -> dict[str, Any]:
    store = _store(request)
    limits = _limits(request)
    job_id = ""
    uploads: list[object] = [file]
    result: dict[str, Any] | None = None
    primary_error: BaseException | None = None
    job_id = uuid.uuid4().hex
    temp_dir = store.jobs_root / f"{_CREATING_PREFIX}{job_id}"
    final_dir = store.jobs_root / job_id
    owns_temp_dir = False
    published_final_dir = False
    draft_created = False
    try:
        form = await request.form()
        file_parts = list(form.getlist("file"))
        uploads = list(file_parts)
        if all(candidate is not file for candidate in uploads):
            uploads.append(file)
        if not uploads:
            uploads = [file]
        if len(file_parts) != 1 or not callable(getattr(file_parts[0], "close", None)):
            raise UploadValidationError("file 字段必须且只能出现一次")
        if not _JOB_ID_RE.fullmatch(job_id):
            raise ValueError("uuid4 生成了无效 ingestion job_id")
        temp_dir, source_dir = _create_source_layout(store, job_id)
        owns_temp_dir = True
        source_path = source_dir / _safe_original_name(file.filename)
        try:
            await save_upload_file(
                file,
                source_path,
                max_bytes=limits.max_upload_bytes,
            )
        except UploadValidationError as exc:
            if str(exc) == _UPLOAD_TOO_LARGE_DETAIL:
                raise _ActualUploadTooLarge(str(exc)) from exc
            raise
        _fsync_directory(source_dir)
        preflight = preflight_upload(source_path, target=target, limits=limits)
        final_dir, source_path = _publish_source_layout(
            store,
            job_id,
            temp_dir,
            source_path.name,
        )
        owns_temp_dir = False
        published_final_dir = True
        _fsync_directory(store.jobs_root)
        result = store.create_draft(
            preflight=preflight,
            source_path=source_path,
            job_id=job_id,
        )
        draft_created = True
        (final_dir / _CREATING_MARKER).unlink()
        _fsync_directory(final_dir)
    except BaseException as exc:
        primary_error = exc

    close_error = await _close_uploads(uploads)
    if close_error is not None and (
        primary_error is None
        or (
            not isinstance(close_error, Exception)
            and isinstance(primary_error, Exception)
        )
    ):
        primary_error = close_error
    elif close_error is not None:
        logger.error(
            "关闭上传文件失败 job_id=%s",
            job_id,
            exc_info=_exception_info(close_error),
        )

    if primary_error is not None:
        _rollback_failed_create(
            store,
            job_id,
            temp_dir=temp_dir,
            final_dir=final_dir,
            owns_temp_dir=owns_temp_dir,
            published_final_dir=published_final_dir,
            draft_created=draft_created,
        )
        if not isinstance(primary_error, Exception):
            raise primary_error
        if isinstance(primary_error, _ActualUploadTooLarge):
            raise HTTPException(status_code=413, detail=_UPLOAD_TOO_LARGE_DETAIL) from primary_error
        if isinstance(primary_error, UploadValidationError):
            raise HTTPException(status_code=400, detail=_UPLOAD_INVALID_DETAIL) from primary_error
        logger.error(
            "创建入库任务失败 job_id=%s",
            job_id,
            exc_info=_exception_info(primary_error),
        )
        raise HTTPException(status_code=500, detail=_STORAGE_UNAVAILABLE_DETAIL) from primary_error

    assert result is not None
    return result


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
