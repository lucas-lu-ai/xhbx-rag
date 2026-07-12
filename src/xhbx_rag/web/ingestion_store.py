from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from xhbx_rag.web.ingestion_uploads import PreflightResult
from xhbx_rag.web.source_paths import project_root_from_module


_DEFAULT_DB_PATH = Path(".local/web_ingestion/ingestion.sqlite3")
_DEFAULT_JOBS_PATH = Path(".local/web_ingestion/jobs")
_MAX_PERSISTED_TEXT_CHARS = 2_000
_MAX_EVENT_PAYLOAD_BYTES = 16 * 1_024
_REDACTED_PATH = "[已隐藏路径]"
_REDACTED_SECRET = "[已隐藏敏感信息]"
_UNSAFE_STACK_DETAIL = "内部处理信息已隐藏"
_SENSITIVE_KEY_RE = re.compile(
    r"^(?:bearer|(?:.*[_-])?(?:api[_-]key|token|secret|password))$", re.IGNORECASE
)
_SENSITIVE_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9_])[\"']?"
    r"((?:[A-Za-z0-9.]+[_-])*(?:api[_-]key|token|secret|password)|Bearer)"
    r"[\"']?\s*[:=]\s*"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;，；。]+)",
    re.IGNORECASE,
)
_BEARER_TOKEN_RE = re.compile(r"\bBearer\s+[^\s,;，；。]+", re.IGNORECASE)
_UNC_ABSOLUTE_PATH_RE = re.compile(
    r"\\\\[^\\/\s]+[\\/]"
    r"(?:[^\\/\s]+[\\/])*[^\\/\s,;，；。)\]}]+"
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]"
    r"(?:[^\\/\s]+[\\/])*[^\\/\s,;)\]}]+"
)
_POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![:/])/(?!/)(?:[^/\s]+/)*[^/\s,;)\]}]+"
)
_STACK_STRUCTURE_RE = re.compile(
    r"\b(?:traceback|stack\s*trace|exception\s*chain)\b"
    r"|during handling of the above exception"
    r"|the above exception was the direct cause"
    r"|(?:^|\n)\s*File\s+[\"'].*?,\s*line\s+\d+"
    r"|(?:^|\n)\s*at\s+\S+\([^)]*\)",
    re.IGNORECASE,
)
_SAFE_INGESTION_ERROR_DETAILS = {
    "upload_invalid": "上传文件无效",
    "upload_too_large": "上传文件超过大小限制",
    "parse_failed": "文档解析失败",
    "chunk_failed": "文档切分失败",
    "embedding_failed": "向量生成失败",
    "index_failed": "知识库写入失败",
    "rollback_pending": "知识库恢复尚未完成",
    "service_restarted": "服务重启导致任务中断，请从头重试",
    "storage_unavailable": "任务存储暂时不可用",
}
_SAFE_STAGE_MESSAGES = {
    "uploaded": "上传已完成",
    "parsing": "正在解析文档",
    "chunking": "正在切分文档",
    "indexing": "正在写入知识库",
    "completed": "任务已完成",
}
_UNKNOWN_STAGE_MESSAGE = "任务处理中"
_ITEM_FAILURE_DETAIL = "输入项处理失败"
_RETRY_CLEANUP_FAILURE_DETAIL = "清理旧任务产物失败"

_PUBLIC_JOB_FIELDS = (
    "job_id",
    "source_name",
    "source_kind",
    "target",
    "status",
    "current_stage",
    "attempt_count",
    "item_total",
    "item_done",
    "document_total",
    "chunk_total",
    "ignored_total",
    "warning_count",
    "error_code",
    "error_detail",
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: object) -> str:
    text = str(value)
    if _STACK_STRUCTURE_RE.search(text):
        return _UNSAFE_STACK_DETAIL
    text = _SENSITIVE_VALUE_RE.sub(lambda match: f"{match.group(1)}={_REDACTED_SECRET}", text)
    text = _BEARER_TOKEN_RE.sub(f"Bearer {_REDACTED_SECRET}", text)
    text = _UNC_ABSOLUTE_PATH_RE.sub(_REDACTED_PATH, text)
    text = _WINDOWS_ABSOLUTE_PATH_RE.sub(_REDACTED_PATH, text)
    text = _POSIX_ABSOLUTE_PATH_RE.sub(_REDACTED_PATH, text)
    return text[:_MAX_PERSISTED_TEXT_CHARS]


def _safe_payload_value(value: object) -> object:
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            key = _safe_text(raw_key)
            sanitized[key] = (
                _REDACTED_SECRET
                if _SENSITIVE_KEY_RE.fullmatch(str(raw_key))
                else _safe_payload_value(raw_value)
            )
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_safe_payload_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_text(value)


def _safe_payload_json(payload: object) -> str:
    try:
        sanitized = _safe_payload_value(payload)
        encoded = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))
    except (RecursionError, TypeError, ValueError):
        encoded = json.dumps({"truncated": True})
    if len(encoded.encode("utf-8")) > _MAX_EVENT_PAYLOAD_BYTES:
        return json.dumps({"truncated": True})
    return encoded


def _safe_ingestion_error(code: object) -> tuple[str, str]:
    normalized_code = str(code)
    if normalized_code not in _SAFE_INGESTION_ERROR_DETAILS:
        normalized_code = "storage_unavailable"
    return normalized_code, _SAFE_INGESTION_ERROR_DETAILS[normalized_code]


def _validate_source_layout(jobs_root: Path, job_id: str, source_path: Path) -> Path:
    raw = Path(source_path)
    if not raw.is_absolute() or any(part in {".", ".."} for part in raw.parts):
        raise ValueError("source path 必须是无 dot segment 的绝对路径")
    expected = jobs_root / job_id / "source" / raw.name
    if str(raw) != str(expected) or not raw.name or raw.name in {".", ".."}:
        raise ValueError("source path 不符合 job workspace 布局")
    chain = (jobs_root, jobs_root / job_id, jobs_root / job_id / "source", raw)
    for index, component in enumerate(chain):
        try:
            metadata = component.lstat()
        except OSError as exc:
            raise ValueError("source workspace 不存在") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("source workspace 不允许符号链接")
        if index == len(chain) - 1:
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("source 必须是常规文件")
        elif not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("source workspace 组件必须是目录")
        if not component.resolve(strict=True).is_relative_to(jobs_root):
            raise ValueError("source workspace 越界")
    return raw


def _absolute_lexical_path(value: Path) -> str:
    raw = os.fspath(value)
    if (
        not Path(raw).is_absolute()
        or any(segment in {".", ".."} for segment in raw.split(os.sep))
        or str(Path(raw)) != raw
    ):
        raise IngestionStateError("路径必须是无 dot segment 的 absolute lexical path")
    return raw


def _normalized_jobs_root(value: Path) -> Path:
    raw = Path(value)
    if not raw.is_absolute() or ".." in raw.parts:
        raise ValueError("jobs_root 必须是无 dot segment 的绝对路径")
    try:
        metadata = raw.lstat()
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("jobs_root 不允许是符号链接")
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("jobs_root 必须是目录")
    # root 尚不存在时，resolve(strict=False) 只规范化已存在祖先的别名，
    # 不创建目录，也不会越过上面的 root 自身 symlink 拒绝规则。
    normalized = raw.resolve(strict=False)
    if not normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError("jobs_root 规范化失败")
    return normalized


class IngestionStateError(RuntimeError):
    """入库任务或 attempt 不满足所请求的状态转换。"""


@dataclass(frozen=True)
class RecoveryAction:
    job_id: str
    action: Literal[
        "enqueue",
        "fail_interrupted",
        "rollback",
        "finish_committed",
        "cleanup_succeeded",
        "delete",
    ]
    journal_path: Path | None = None


class IngestionStore:
    def __init__(
        self, db_path: Path | None = None, *, jobs_root: Path | None = None
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else _DEFAULT_DB_PATH
        if db_path is None:
            self.db_path = project_root_from_module() / self.db_path
        configured_jobs_root = (
            Path(jobs_root)
            if jobs_root is not None
            else (
                project_root_from_module() / _DEFAULT_JOBS_PATH
                if db_path is None
                else self.db_path.parent / "jobs"
            )
        )
        self.jobs_root = _normalized_jobs_root(configured_jobs_root)
        self._strict_source_layout = jobs_root is not None or db_path is None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ingestion_jobs (
                    job_id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    source_kind TEXT NOT NULL CHECK (source_kind IN ('file', 'zip')),
                    source_path TEXT NOT NULL,
                    target TEXT NOT NULL CHECK (target IN ('case', 'course')),
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'draft', 'queued', 'running', 'rolling_back',
                            'succeeded', 'failed', 'deleting'
                        )
                    ),
                    current_stage TEXT NOT NULL CHECK (
                        current_stage IN ('uploaded', 'parsing', 'chunking', 'indexing', 'completed')
                    ),
                    attempt_count INTEGER NOT NULL,
                    item_total INTEGER NOT NULL,
                    item_done INTEGER NOT NULL,
                    document_total INTEGER NOT NULL,
                    chunk_total INTEGER NOT NULL,
                    ignored_total INTEGER NOT NULL,
                    ignored_entries_json TEXT NOT NULL,
                    warning_count INTEGER NOT NULL,
                    error_code TEXT,
                    error_detail TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS ingestion_items (
                    job_id TEXT NOT NULL REFERENCES ingestion_jobs(job_id) ON DELETE CASCADE,
                    item_index INTEGER NOT NULL,
                    unit_key TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    relative_paths_json TEXT NOT NULL,
                    document_count INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')
                    ),
                    current_stage TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    warning_count INTEGER NOT NULL,
                    error_detail TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, item_index)
                );

                CREATE TABLE IF NOT EXISTS ingestion_attempts (
                    job_id TEXT NOT NULL REFERENCES ingestion_jobs(job_id) ON DELETE CASCADE,
                    attempt_no INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('queued', 'running', 'succeeded', 'failed', 'rolling_back')
                    ),
                    current_stage TEXT NOT NULL,
                    commit_state TEXT NOT NULL CHECK (
                        commit_state IN (
                            'not_started', 'prepared', 'committed', 'rolling_back', 'rolled_back'
                        )
                    ),
                    workspace_path TEXT,
                    journal_path TEXT,
                    content_sha256 TEXT,
                    error_code TEXT,
                    error_detail TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    PRIMARY KEY (job_id, attempt_no)
                );

                CREATE TABLE IF NOT EXISTS ingestion_events (
                    job_id TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, attempt_no, sequence),
                    FOREIGN KEY (job_id, attempt_no)
                        REFERENCES ingestion_attempts(job_id, attempt_no) ON DELETE CASCADE
                );
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(ingestion_attempts)")
            }
            if "content_sha256" not in columns:
                connection.execute(
                    "ALTER TABLE ingestion_attempts ADD COLUMN content_sha256 TEXT"
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_draft(
        self,
        *,
        preflight: PreflightResult,
        source_path: Path,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        job_id = job_id or uuid.uuid4().hex
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("job_id 必须是 UUID hex")
        if self._strict_source_layout and Path(source_path).name != preflight.source_name:
            raise ValueError("source filename 与预检不一致")
        stored_source = (
            _validate_source_layout(self.jobs_root, job_id, source_path)
            if self._strict_source_layout
            else Path(os.path.abspath(os.fspath(source_path)))
        )
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO ingestion_jobs (
                    job_id, source_name, source_kind, source_path, target, status,
                    current_stage, attempt_count, item_total, item_done, document_total,
                    chunk_total, ignored_total, ignored_entries_json, warning_count,
                    error_code, error_detail, created_at, updated_at, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, 'draft', 'uploaded', 0, ?, 0, ?, 0, ?, ?, 0,
                          NULL, NULL, ?, ?, NULL, NULL)
                """,
                (
                    job_id,
                    preflight.source_name,
                    preflight.source_kind,
                    str(stored_source),
                    preflight.target,
                    len(preflight.items),
                    preflight.document_total,
                    preflight.ignored_total,
                    json.dumps(preflight.ignored_entries, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            connection.executemany(
                """
                INSERT INTO ingestion_items (
                    job_id, item_index, unit_key, display_name, relative_paths_json,
                    document_count, status, current_stage, chunk_count, warning_count,
                    error_detail, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 'uploaded', 0, 0, NULL, ?)
                """,
                (
                    (
                        job_id,
                        item.item_index,
                        item.unit_key,
                        item.display_name,
                        json.dumps(item.relative_paths, ensure_ascii=False),
                        item.document_count,
                        now,
                    )
                    for item in preflight.items
                ),
            )
        job = self.get_job(job_id)
        assert job is not None
        return job

    def get_job(self, job_id: str, *, include_events: bool = True) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ingestion_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            result = self._public_job(row)
            result["ignored_entries"] = json.loads(row["ignored_entries_json"])
            result["items"] = self._items(connection, job_id)
            result["attempt"] = self._current_attempt(connection, row, internal=False)
            result["events"] = self._events(connection, job_id) if include_events else []
            return result

    def list_jobs(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(0, min(int(limit), 200))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM ingestion_jobs
                ORDER BY created_at DESC, job_id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
            return [self._public_job(row) for row in rows]

    def get_job_for_execution(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ingestion_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            result = self._public_job(row)
            result["source_path"] = Path(row["source_path"])
            result["jobs_root"] = self.jobs_root
            result["ignored_entries"] = json.loads(row["ignored_entries_json"])
            result["items"] = self._items(connection, job_id)
            result["attempt"] = self._current_attempt(connection, row, internal=True)
            return result

    def get_progress(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ingestion_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            active_item = connection.execute(
                """
                SELECT item_index FROM ingestion_items
                WHERE job_id = ? AND status = 'running'
                ORDER BY item_index LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            message = connection.execute(
                """
                SELECT message FROM ingestion_events
                WHERE job_id = ? AND attempt_no = ?
                ORDER BY created_at DESC, sequence DESC LIMIT 1
                """,
                (job_id, row["attempt_count"]),
            ).fetchone()
            return {
                "job_id": row["job_id"],
                "status": row["status"],
                "current_stage": row["current_stage"],
                "attempt_no": row["attempt_count"] or None,
                "item_total": row["item_total"],
                "item_done": row["item_done"],
                "document_total": row["document_total"],
                "chunk_total": row["chunk_total"],
                "warning_count": row["warning_count"],
                "active_item_index": active_item["item_index"] if active_item else None,
                "message": message["message"] if message else None,
                "updated_at": row["updated_at"],
            }

    def start_job(self, job_id: str) -> str:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'queued', attempt_count = 1, updated_at = ?,
                    error_code = NULL, error_detail = NULL, finished_at = NULL
                WHERE job_id = ? AND status = 'draft'
                """,
                (now, job_id),
            )
            if cursor.rowcount != 1:
                return "conflict"
            connection.execute(
                """
                INSERT INTO ingestion_attempts (
                    job_id, attempt_no, status, current_stage, commit_state,
                    workspace_path, journal_path, error_code, error_detail,
                    started_at, finished_at
                ) VALUES (?, 1, 'queued', 'uploaded', 'not_started',
                          NULL, NULL, NULL, NULL, NULL, NULL)
                """,
                (job_id,),
            )
        return "ok"

    def claim_job(self, job_id: str) -> bool:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'running', started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (now, now, job_id),
            )
            return cursor.rowcount == 1

    def begin_attempt(self, job_id: str, workspace_path: Path) -> int:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT attempt_count FROM ingestion_jobs
                WHERE job_id = ? AND status = 'running'
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("任务不在可开始 attempt 的状态")
            attempt_no = int(row["attempt_count"])
            cursor = connection.execute(
                """
                UPDATE ingestion_attempts
                SET status = 'running', workspace_path = ?, started_at = ?
                WHERE job_id = ? AND attempt_no = ? AND status = 'queued'
                """,
                (_absolute_lexical_path(workspace_path), now, job_id, attempt_no),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("当前 attempt 不在 queued 状态")
            self._append_event(
                connection,
                job_id,
                attempt_no,
                event_type="attempt_started",
                message="任务开始执行",
            )
            return attempt_no

    def mark_stage(self, job_id: str, stage: str, message: str) -> None:
        now = _utc_now()
        safe_stage = stage if stage in _SAFE_STAGE_MESSAGES else None
        safe_message = _SAFE_STAGE_MESSAGES.get(stage, _UNKNOWN_STAGE_MESSAGE)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._active_attempt_row(connection, job_id)
            if row is None:
                return
            if safe_stage is None:
                connection.execute(
                    "UPDATE ingestion_jobs SET updated_at = ? WHERE job_id = ?", (now, job_id)
                )
            else:
                connection.execute(
                    "UPDATE ingestion_jobs SET current_stage = ?, updated_at = ? WHERE job_id = ?",
                    (safe_stage, now, job_id),
                )
                connection.execute(
                    """
                    UPDATE ingestion_attempts SET current_stage = ?
                    WHERE job_id = ? AND attempt_no = ?
                    """,
                    (safe_stage, job_id, row["attempt_count"]),
                )
            self._append_event(
                connection,
                job_id,
                row["attempt_count"],
                event_type="stage_changed",
                message=safe_message,
                payload={"stage": safe_stage or "unknown"},
            )

    def mark_content_identity(self, job_id: str, content_sha256: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
            raise IngestionStateError("content_sha256 无效")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._attempt_state_row(connection, job_id)
            if row is None or (
                row["job_status"], row["attempt_status"], row["commit_state"]
            ) != ("running", "running", "not_started"):
                raise IngestionStateError("content identity 只允许在提交开始前写入")
            existing = row["content_sha256"]
            if existing is not None and existing != content_sha256:
                raise IngestionStateError("content identity 不可变")
            if existing is None:
                connection.execute(
                    """
                    UPDATE ingestion_attempts SET content_sha256 = ?
                    WHERE job_id = ? AND attempt_no = ?
                    """,
                    (content_sha256, job_id, row["attempt_count"]),
                )

    def mark_item_running(self, job_id: str, item_index: int, stage: str) -> bool:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._active_attempt_row(connection, job_id)
            if row is None:
                return False
            cursor = connection.execute(
                """
                UPDATE ingestion_items
                SET status = 'running', current_stage = ?, updated_at = ?
                WHERE job_id = ? AND item_index = ? AND status = 'pending'
                """,
                (stage, now, job_id, item_index),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute(
                "UPDATE ingestion_jobs SET current_stage = ?, updated_at = ? WHERE job_id = ?",
                (stage, now, job_id),
            )
            connection.execute(
                """
                UPDATE ingestion_attempts SET current_stage = ?
                WHERE job_id = ? AND attempt_no = ?
                """,
                (stage, job_id, row["attempt_count"]),
            )
            self._append_event(
                connection,
                job_id,
                row["attempt_count"],
                event_type="item_running",
                message=f"开始处理输入项 {item_index}",
                payload={"item_index": item_index, "stage": stage},
            )
            return True

    def complete_item(
        self, job_id: str, item_index: int, chunk_count: int, warning_count: int
    ) -> None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._active_attempt_row(connection, job_id)
            if row is None:
                return
            cursor = connection.execute(
                """
                UPDATE ingestion_items
                SET status = 'succeeded', chunk_count = ?, warning_count = ?, updated_at = ?
                WHERE job_id = ? AND item_index = ? AND status = 'running'
                """,
                (chunk_count, warning_count, now, job_id, item_index),
            )
            if cursor.rowcount != 1:
                return
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET item_done = item_done + 1, chunk_total = chunk_total + ?,
                    warning_count = warning_count + ?, updated_at = ?
                WHERE job_id = ?
                """,
                (chunk_count, warning_count, now, job_id),
            )
            self._append_event(
                connection,
                job_id,
                row["attempt_count"],
                event_type="item_completed",
                message=f"输入项 {item_index} 处理完成",
                payload={
                    "item_index": item_index,
                    "chunk_count": chunk_count,
                    "warning_count": warning_count,
                },
            )

    def fail_item(self, job_id: str, item_index: int, detail: str) -> None:
        now = _utc_now()
        safe_detail = _ITEM_FAILURE_DETAIL
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._active_attempt_row(connection, job_id)
            if row is None:
                return
            cursor = connection.execute(
                """
                UPDATE ingestion_items
                SET status = 'failed', error_detail = ?, updated_at = ?
                WHERE job_id = ? AND item_index = ? AND status IN ('pending', 'running')
                """,
                (safe_detail, now, job_id, item_index),
            )
            if cursor.rowcount != 1:
                return
            self._append_event(
                connection,
                job_id,
                row["attempt_count"],
                event_type="item_failed",
                message=safe_detail,
                payload={"item_index": item_index},
            )

    def mark_commit_state(
        self, job_id: str, state: str, journal_path: Path | None = None
    ) -> None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._attempt_state_row(connection, job_id)
            if row is None:
                raise IngestionStateError("任务或当前 attempt 不存在")
            if state not in {
                "not_started",
                "prepared",
                "committed",
                "rolling_back",
                "rolled_back",
            }:
                raise IngestionStateError(f"不支持的提交状态: {state}")
            current_state = row["commit_state"]
            job_status = row["job_status"]
            attempt_status = row["attempt_status"]
            resolved_journal = (
                _absolute_lexical_path(journal_path)
                if journal_path is not None
                else None
            )
            stored_journal = row["journal_path"]
            if (
                stored_journal is not None
                and resolved_journal is not None
                and resolved_journal != stored_journal
            ):
                raise IngestionStateError("journal_path 在 prepared 后不可变")
            coherent_statuses = {
                "not_started": ("running", "running"),
                "prepared": ("running", "running"),
                "committed": ("running", "running"),
                "rolling_back": ("rolling_back", "rolling_back"),
                "rolled_back": ("rolling_back", "rolling_back"),
            }
            if state == current_state:
                if (job_status, attempt_status) != coherent_statuses[state]:
                    raise IngestionStateError("任务与 attempt 状态不一致")
                return
            allowed_transition = (current_state, state) in {
                ("not_started", "prepared"),
                ("prepared", "committed"),
                ("prepared", "rolling_back"),
                ("rolling_back", "rolled_back"),
            }
            if not allowed_transition:
                raise IngestionStateError(f"非法提交状态转换: {current_state} -> {state}")
            if current_state in ("not_started", "prepared") and (
                job_status,
                attempt_status,
            ) != ("running", "running"):
                raise IngestionStateError("提交状态转换要求 running job/attempt")
            if current_state == "rolling_back" and (
                job_status,
                attempt_status,
            ) != ("rolling_back", "rolling_back"):
                raise IngestionStateError("回滚完成要求 rolling_back job/attempt")
            if state == "prepared" and journal_path is None:
                raise IngestionStateError("prepared 状态必须提供 journal_path")
            connection.execute(
                """
                UPDATE ingestion_attempts
                SET commit_state = ?, journal_path = COALESCE(?, journal_path),
                    status = CASE WHEN ? = 'rolling_back' THEN 'rolling_back' ELSE status END
                WHERE job_id = ? AND attempt_no = ?
                """,
                (state, resolved_journal, state, job_id, row["attempt_count"]),
            )
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = CASE WHEN ? = 'rolling_back' THEN 'rolling_back' ELSE status END,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (state, now, job_id),
            )
            self._append_event(
                connection,
                job_id,
                row["attempt_count"],
                event_type="commit_state_changed",
                message=f"提交状态更新为 {state}",
                payload={"commit_state": state},
            )

    def succeed_job(self, job_id: str, chunk_total: int, warning_count: int) -> None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._attempt_state_row(connection, job_id)
            if row is None or (
                row["job_status"], row["attempt_status"], row["commit_state"]
            ) != ("running", "running", "committed"):
                raise IngestionStateError("成功状态要求 running job/attempt 且已 committed")
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'succeeded', current_stage = 'completed', item_done = item_total,
                    chunk_total = ?, warning_count = ?, error_code = NULL, error_detail = NULL,
                    updated_at = ?, finished_at = ?
                WHERE job_id = ?
                """,
                (chunk_total, warning_count, now, now, job_id),
            )
            connection.execute(
                """
                UPDATE ingestion_attempts
                SET status = 'succeeded', current_stage = 'completed', finished_at = ?
                WHERE job_id = ? AND attempt_no = ?
                """,
                (now, job_id, row["attempt_count"]),
            )
            self._append_event(
                connection,
                job_id,
                row["attempt_count"],
                event_type="job_succeeded",
                message="任务执行成功",
                payload={"chunk_total": chunk_total, "warning_count": warning_count},
            )

    def mark_recovery_cleaned(self, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._attempt_state_row(connection, job_id)
            if row is None or (
                row["job_status"], row["attempt_status"], row["commit_state"]
            ) != ("succeeded", "succeeded", "committed"):
                raise IngestionStateError("恢复材料清理只允许用于已成功提交的任务")
            connection.execute(
                """
                UPDATE ingestion_attempts
                SET journal_path = NULL
                WHERE job_id = ? AND attempt_no = ?
                """,
                (job_id, row["attempt_count"]),
            )

    def mark_rolling_back(self, job_id: str, *, code: str, detail: str) -> None:
        now = _utc_now()
        safe_code, safe_detail = _safe_ingestion_error(code)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._attempt_state_row(connection, job_id)
            if row is None:
                raise IngestionStateError("任务或当前 attempt 不存在")
            state_tuple = (
                row["job_status"],
                row["attempt_status"],
                row["commit_state"],
            )
            if state_tuple not in {
                ("running", "running", "prepared"),
                ("rolling_back", "rolling_back", "rolling_back"),
            }:
                raise IngestionStateError("回滚只允许从 prepared 或 rolling_back 状态开始")
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'rolling_back', error_code = ?, error_detail = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (safe_code, safe_detail, now, job_id),
            )
            connection.execute(
                """
                UPDATE ingestion_attempts
                SET status = 'rolling_back', commit_state = 'rolling_back',
                    error_code = ?, error_detail = ?
                WHERE job_id = ? AND attempt_no = ?
                """,
                (safe_code, safe_detail, job_id, row["attempt_count"]),
            )
            self._append_event(
                connection,
                job_id,
                row["attempt_count"],
                event_type="rollback_started",
                message=safe_detail,
            )

    def fail_job(self, job_id: str, *, code: str, detail: str) -> None:
        now = _utc_now()
        safe_code, safe_detail = _safe_ingestion_error(code)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT j.attempt_count, j.status AS job_status,
                       a.status AS attempt_status, a.commit_state
                FROM ingestion_jobs AS j
                JOIN ingestion_attempts AS a
                  ON a.job_id = j.job_id AND a.attempt_no = j.attempt_count
                WHERE j.job_id = ?
                """,
                (job_id,),
            ).fetchone()
            allowed = row is not None and (
                (
                    row["job_status"] == "running"
                    and row["attempt_status"] in ("queued", "running")
                    and row["commit_state"] == "not_started"
                )
                or (
                    row["job_status"] == "rolling_back"
                    and row["attempt_status"] == "rolling_back"
                    and row["commit_state"] == "rolled_back"
                )
            )
            if not allowed:
                raise IngestionStateError("失败状态要求未提交的 queued/running attempt 或已完成回滚")
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'failed', error_code = ?, error_detail = ?,
                    updated_at = ?, finished_at = ?
                WHERE job_id = ?
                """,
                (safe_code, safe_detail, now, now, job_id),
            )
            connection.execute(
                """
                UPDATE ingestion_items
                SET status = CASE
                        WHEN status = 'running' THEN 'failed'
                        ELSE 'skipped'
                    END,
                    error_detail = CASE
                        WHEN status = 'running' THEN ?
                        ELSE error_detail
                    END,
                    updated_at = ?
                WHERE job_id = ? AND status IN ('running', 'pending')
                """,
                (_ITEM_FAILURE_DETAIL, now, job_id),
            )
            if row["attempt_count"]:
                connection.execute(
                    """
                    UPDATE ingestion_attempts
                    SET status = 'failed', error_code = ?, error_detail = ?, finished_at = ?
                    WHERE job_id = ? AND attempt_no = ?
                    """,
                    (safe_code, safe_detail, now, job_id, row["attempt_count"]),
                )
                self._append_event(
                    connection,
                    job_id,
                    row["attempt_count"],
                    event_type="job_failed",
                    message=safe_detail,
                    payload={"error_code": safe_code},
                )

    def retry_job(self, job_id: str) -> dict[str, object]:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT attempt_count FROM ingestion_jobs WHERE job_id = ? AND status = 'failed'",
                (job_id,),
            ).fetchone()
            if row is None:
                return {"result": "conflict"}
            attempt_no = int(row["attempt_count"]) + 1
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'queued', current_stage = 'uploaded', attempt_count = ?,
                    item_done = 0, chunk_total = 0, warning_count = 0,
                    error_code = NULL, error_detail = NULL, updated_at = ?,
                    started_at = NULL, finished_at = NULL
                WHERE job_id = ?
                """,
                (attempt_no, now, job_id),
            )
            connection.execute(
                """
                UPDATE ingestion_items
                SET status = 'pending', current_stage = 'uploaded', chunk_count = 0,
                    warning_count = 0, error_detail = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (now, job_id),
            )
            connection.execute(
                """
                INSERT INTO ingestion_attempts (
                    job_id, attempt_no, status, current_stage, commit_state,
                    workspace_path, journal_path, error_code, error_detail,
                    started_at, finished_at
                ) VALUES (?, ?, 'queued', 'uploaded', 'not_started',
                          NULL, NULL, NULL, NULL, NULL, NULL)
                """,
                (job_id, attempt_no),
            )
        return {"result": "ok", "attempt_no": attempt_no}

    def abort_retry(self, job_id: str, attempt_no: int, detail: str) -> None:
        now = _utc_now()
        safe_detail = _RETRY_CLEANUP_FAILURE_DETAIL
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT attempt_count FROM ingestion_jobs
                WHERE job_id = ? AND status = 'queued' AND attempt_count = ?
                """,
                (job_id, attempt_no),
            ).fetchone()
            if row is None:
                return
            cursor = connection.execute(
                """
                DELETE FROM ingestion_attempts
                WHERE job_id = ? AND attempt_no = ? AND status = 'queued'
                """,
                (job_id, attempt_no),
            )
            if cursor.rowcount != 1:
                return
            previous_attempt = attempt_no - 1
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'failed', attempt_count = ?, error_code = 'storage_unavailable',
                    error_detail = ?, updated_at = ?, finished_at = ?
                WHERE job_id = ?
                """,
                (previous_attempt, safe_detail, now, now, job_id),
            )
            if previous_attempt > 0:
                self._append_event(
                    connection,
                    job_id,
                    previous_attempt,
                    event_type="retry_aborted",
                    message=safe_detail,
                )

    def begin_delete(self, job_id: str) -> str:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM ingestion_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return "not_found"
            if row["status"] == "deleting":
                return "ok"
            if row["status"] not in ("draft", "succeeded", "failed"):
                return "conflict"
            connection.execute(
                "UPDATE ingestion_jobs SET status = 'deleting', updated_at = ? WHERE job_id = ?",
                (now, job_id),
            )
        return "ok"

    def finish_delete(self, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM ingestion_jobs WHERE job_id = ? AND status = 'deleting'", (job_id,)
            )

    def recovery_actions(self) -> list[RecoveryAction]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT j.job_id, j.status, a.commit_state, a.journal_path
                FROM ingestion_jobs AS j
                LEFT JOIN ingestion_attempts AS a
                    ON a.job_id = j.job_id AND a.attempt_no = j.attempt_count
                WHERE j.status IN ('queued', 'running', 'rolling_back', 'deleting')
                   OR (j.status = 'succeeded' AND a.journal_path IS NOT NULL)
                ORDER BY j.created_at, j.job_id
                """
            ).fetchall()

        actions: list[RecoveryAction] = []
        for row in rows:
            status = row["status"]
            commit_state = row["commit_state"]
            journal_path = Path(row["journal_path"]) if row["journal_path"] else None
            if status == "queued":
                action = "enqueue"
                journal_path = None
            elif status == "deleting":
                action = "delete"
                journal_path = None
            elif status == "succeeded":
                action = "cleanup_succeeded"
                journal_path = None
            elif commit_state == "committed":
                action = "finish_committed"
            elif commit_state in ("prepared", "rolling_back"):
                action = "rollback"
            else:
                action = "fail_interrupted"
                journal_path = None
            actions.append(
                RecoveryAction(job_id=row["job_id"], action=action, journal_path=journal_path)
            )
        return actions

    @staticmethod
    def _public_job(row: sqlite3.Row) -> dict[str, Any]:
        return {field: row[field] for field in _PUBLIC_JOB_FIELDS}

    @staticmethod
    def _items(connection: sqlite3.Connection, job_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT * FROM ingestion_items WHERE job_id = ? ORDER BY item_index", (job_id,)
        ).fetchall()
        return [
            {
                "item_index": row["item_index"],
                "unit_key": row["unit_key"],
                "display_name": row["display_name"],
                "relative_paths": json.loads(row["relative_paths_json"]),
                "document_count": row["document_count"],
                "status": row["status"],
                "current_stage": row["current_stage"],
                "chunk_count": row["chunk_count"],
                "warning_count": row["warning_count"],
                "error_detail": row["error_detail"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _current_attempt(
        connection: sqlite3.Connection, row: sqlite3.Row, *, internal: bool
    ) -> dict[str, Any] | None:
        attempt_count = int(row["attempt_count"])
        if attempt_count == 0:
            return None
        attempt = connection.execute(
            """
            SELECT * FROM ingestion_attempts
            WHERE job_id = ? AND attempt_no = ?
            """,
            (row["job_id"], attempt_count),
        ).fetchone()
        if attempt is None:
            return None
        fields = [
            "attempt_no",
            "status",
            "current_stage",
            "commit_state",
            "error_code",
            "error_detail",
            "started_at",
            "finished_at",
        ]
        if internal:
            fields.extend(("workspace_path", "journal_path", "content_sha256"))
        result = {field: attempt[field] for field in fields}
        if internal:
            for path_field in ("workspace_path", "journal_path"):
                if result[path_field] is not None:
                    result[path_field] = Path(result[path_field])
        return result

    @staticmethod
    def _active_attempt_row(
        connection: sqlite3.Connection, job_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT attempt_count FROM ingestion_jobs
            WHERE job_id = ? AND status IN ('running', 'rolling_back')
            """,
            (job_id,),
        ).fetchone()

    @staticmethod
    def _attempt_state_row(
        connection: sqlite3.Connection, job_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT j.attempt_count, j.status AS job_status,
                   a.status AS attempt_status, a.commit_state, a.journal_path,
                   a.content_sha256
            FROM ingestion_jobs AS j
            JOIN ingestion_attempts AS a
              ON a.job_id = j.job_id AND a.attempt_no = j.attempt_count
            WHERE j.job_id = ?
            """,
            (job_id,),
        ).fetchone()

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        job_id: str,
        attempt_no: int,
        *,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        event_type = _safe_text(event_type)
        message = _safe_text(message)
        safe_payload_json = _safe_payload_json(payload or {})
        safe_payload = json.loads(safe_payload_json)
        event_count = connection.execute(
            """
            SELECT COUNT(*) AS event_count FROM ingestion_events
            WHERE job_id = ? AND attempt_no = ?
            """,
            (job_id, attempt_no),
        ).fetchone()["event_count"]
        compactable_types = ("item_running", "item_completed")
        if event_count >= 2_000 and event_type in compactable_types:
            compacted = connection.execute(
                """
                SELECT sequence, payload_json FROM ingestion_events
                WHERE job_id = ? AND attempt_no = ? AND event_type = 'progress_compacted'
                ORDER BY sequence DESC LIMIT 1
                """,
                (job_id, attempt_no),
            ).fetchone()
            compacted_payload: dict[str, Any]
            if compacted is not None:
                compacted_payload = json.loads(compacted["payload_json"])
                compacted_payload["compacted_count"] = (
                    int(compacted_payload.get("compacted_count", 1)) + 1
                )
                compacted_payload["latest_event_type"] = event_type
                compacted_payload["latest_payload"] = safe_payload
                connection.execute(
                    """
                    UPDATE ingestion_events
                    SET message = ?, payload_json = ?, created_at = ?
                    WHERE job_id = ? AND attempt_no = ? AND sequence = ?
                    """,
                    (
                        message,
                        _safe_payload_json(compacted_payload),
                        _utc_now(),
                        job_id,
                        attempt_no,
                        compacted["sequence"],
                    ),
                )
                return
            replaceable = connection.execute(
                """
                SELECT sequence FROM ingestion_events
                WHERE job_id = ? AND attempt_no = ?
                  AND event_type IN ('item_running', 'item_completed')
                ORDER BY sequence DESC LIMIT 1
                """,
                (job_id, attempt_no),
            ).fetchone()
            if replaceable is not None:
                compacted_payload = {
                    "compacted_count": 2,
                    "latest_event_type": event_type,
                    "latest_payload": safe_payload,
                }
                connection.execute(
                    """
                    UPDATE ingestion_events
                    SET event_type = 'progress_compacted', message = ?,
                        payload_json = ?, created_at = ?
                    WHERE job_id = ? AND attempt_no = ? AND sequence = ?
                    """,
                    (
                        message,
                        _safe_payload_json(compacted_payload),
                        _utc_now(),
                        job_id,
                        attempt_no,
                        replaceable["sequence"],
                    ),
                )
                return

        if event_count >= 2_000:
            disposable = connection.execute(
                """
                SELECT sequence FROM ingestion_events
                WHERE job_id = ? AND attempt_no = ?
                  AND event_type IN ('item_running', 'item_completed', 'progress_compacted')
                ORDER BY sequence LIMIT 1
                """,
                (job_id, attempt_no),
            ).fetchone()
            if disposable is None:
                return
            connection.execute(
                """
                DELETE FROM ingestion_events
                WHERE job_id = ? AND attempt_no = ? AND sequence = ?
                """,
                (job_id, attempt_no, disposable["sequence"]),
            )

        sequence = connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
            FROM ingestion_events WHERE job_id = ? AND attempt_no = ?
            """,
            (job_id, attempt_no),
        ).fetchone()["next_sequence"]
        connection.execute(
            """
            INSERT INTO ingestion_events (
                job_id, attempt_no, sequence, event_type, message, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                attempt_no,
                sequence,
                event_type,
                message,
                safe_payload_json,
                _utc_now(),
            ),
        )

    @staticmethod
    def _events(connection: sqlite3.Connection, job_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT * FROM (
                SELECT attempt_no, sequence, event_type, message, payload_json, created_at
                FROM ingestion_events
                WHERE job_id = ?
                ORDER BY created_at DESC, attempt_no DESC, sequence DESC
                LIMIT 200
            )
            ORDER BY created_at, attempt_no, sequence
            """,
            (job_id,),
        ).fetchall()
        return [
            {
                "attempt_no": row["attempt_no"],
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "message": row["message"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
