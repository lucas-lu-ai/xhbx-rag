"""批量执行会话的 SQLite 持久化存储。

- 数据库文件默认位于 <project_root>/.local/web_batch/batch_runs.sqlite3。
- 所有 SQL 全部参数化；读-改-写统一走 BEGIN IMMEDIATE 事务。
- 时间戳统一使用带时区的 ISO8601（datetime.now(timezone.utc).isoformat()）。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from uuid import uuid4

from .source_paths import project_root_from_module

BATCH_DB_RELATIVE_PATH = Path(".local") / "web_batch" / "batch_runs.sqlite3"
DEFAULT_LIST_LIMIT = 200
_CONNECT_TIMEOUT_SECONDS = 5
_TERMINAL_ROW_STATUSES = ("succeeded", "failed")
_ACTIVE_ROW_STATUSES = ("pending", "running")

_CREATE_RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS batch_runs (
    run_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_label TEXT NOT NULL,
    source_format TEXT NOT NULL,
    headers_json TEXT NOT NULL,
    rows_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_CREATE_QUESTIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS batch_questions (
    run_id TEXT NOT NULL,
    row_index INTEGER NOT NULL,
    query TEXT NOT NULL,
    input_answer TEXT NOT NULL,
    top_n INTEGER NOT NULL,
    top_k INTEGER NOT NULL,
    status TEXT NOT NULL,
    response_json TEXT,
    error TEXT,
    bad_case_json TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, row_index)
)
"""

_AGGREGATE_SQL = """
SELECT
    COUNT(*) AS question_total,
    COALESCE(SUM(status = 'succeeded'), 0) AS question_done,
    COALESCE(SUM(status = 'failed'), 0) AS question_failed
FROM batch_questions
WHERE run_id = ?
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BatchRunStore:
    """批量会话与批量行的 SQLite 存储。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = (
            Path(db_path)
            if db_path is not None
            else project_root_from_module() / BATCH_DB_RELATIVE_PATH
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ---------------------------------------------------------------- 连接

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=_CONNECT_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_CREATE_RUNS_TABLE_SQL)
            conn.execute(_CREATE_QUESTIONS_TABLE_SQL)

    # ---------------------------------------------------------------- 创建

    def create_run(
        self,
        *,
        title: str,
        source_label: str,
        source_format: str,
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        questions: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        run_id = uuid4().hex
        now = _utc_now_iso()
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO batch_runs (
                    run_id, title, source_label, source_format,
                    headers_json, rows_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    run_id,
                    title,
                    source_label,
                    source_format,
                    json.dumps(list(headers), ensure_ascii=False),
                    json.dumps([list(row) for row in rows], ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.executemany(
                """
                INSERT INTO batch_questions (
                    run_id, row_index, query, input_answer, top_n, top_k,
                    status, response_json, error, bad_case_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL, ?)
                """,
                [
                    (
                        run_id,
                        int(question["row_index"]),
                        str(question["query"]),
                        str(question.get("input_answer", "")),
                        int(question["top_n"]),
                        int(question["top_k"]),
                        now,
                    )
                    for question in questions
                ],
            )
        return {
            "run_id": run_id,
            "title": title,
            "status": "pending",
            "source_label": source_label,
            "source_format": source_format,
            "question_total": len(questions),
            "question_done": 0,
            "question_failed": 0,
            "created_at": now,
            "updated_at": now,
        }

    # ---------------------------------------------------------------- 查询

    def list_runs(self, limit: int = DEFAULT_LIST_LIMIT) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    r.run_id, r.title, r.status, r.source_label, r.source_format,
                    r.created_at, r.updated_at,
                    COUNT(q.row_index) AS question_total,
                    COALESCE(SUM(q.status = 'succeeded'), 0) AS question_done,
                    COALESCE(SUM(q.status = 'failed'), 0) AS question_failed
                FROM batch_runs r
                LEFT JOIN batch_questions q ON q.run_id = r.run_id
                GROUP BY r.run_id
                ORDER BY r.created_at DESC, r.rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._listing_entry_from_row(row) for row in rows]

    def get_run(
        self,
        run_id: str,
        *,
        include_table: bool = False,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            run_row = conn.execute(
                "SELECT * FROM batch_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run_row is None:
                return None
            aggregate = conn.execute(_AGGREGATE_SQL, (run_id,)).fetchone()
            question_rows = conn.execute(
                """
                SELECT row_index, query, input_answer, top_n, top_k,
                       status, response_json, error, bad_case_json, updated_at
                FROM batch_questions
                WHERE run_id = ?
                ORDER BY row_index ASC
                """,
                (run_id,),
            ).fetchall()

        detail: dict[str, Any] = {
            "run_id": run_row["run_id"],
            "title": run_row["title"],
            "status": run_row["status"],
            "source_label": run_row["source_label"],
            "source_format": run_row["source_format"],
            "question_total": int(aggregate["question_total"]),
            "question_done": int(aggregate["question_done"]),
            "question_failed": int(aggregate["question_failed"]),
            "created_at": run_row["created_at"],
            "updated_at": run_row["updated_at"],
            "questions": [self._question_from_row(row) for row in question_rows],
        }
        if include_table:
            detail["headers"] = json.loads(run_row["headers_json"])
            detail["rows"] = json.loads(run_row["rows_json"])
        return detail

    def get_progress(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            run_row = conn.execute(
                "SELECT run_id, status, updated_at FROM batch_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run_row is None:
                return None
            aggregate = conn.execute(_AGGREGATE_SQL, (run_id,)).fetchone()
            question_rows = conn.execute(
                """
                SELECT row_index, status, updated_at
                FROM batch_questions
                WHERE run_id = ?
                ORDER BY row_index ASC
                """,
                (run_id,),
            ).fetchall()
        return {
            "run_id": run_row["run_id"],
            "status": run_row["status"],
            "question_total": int(aggregate["question_total"]),
            "question_done": int(aggregate["question_done"]),
            "question_failed": int(aggregate["question_failed"]),
            "updated_at": run_row["updated_at"],
            "questions": [
                {
                    "row_index": row["row_index"],
                    "status": row["status"],
                    "updated_at": row["updated_at"],
                }
                for row in question_rows
            ],
        }

    def get_question(self, run_id: str, row_index: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT row_index, query, input_answer, top_n, top_k,
                       status, response_json, error, bad_case_json, updated_at
                FROM batch_questions
                WHERE run_id = ? AND row_index = ?
                """,
                (run_id, row_index),
            ).fetchone()
        if row is None:
            return None
        return self._question_from_row(row)

    def fetch_pending_row_indexes(self, run_id: str) -> list[int]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT row_index FROM batch_questions
                WHERE run_id = ? AND status = 'pending'
                ORDER BY row_index ASC
                """,
                (run_id,),
            ).fetchall()
        return [row["row_index"] for row in rows]

    # ---------------------------------------------------------------- 状态机

    def claim_run(self, run_id: str) -> bool:
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE batch_runs SET status = 'running', updated_at = ?
                WHERE run_id = ? AND status = 'pending'
                """,
                (_utc_now_iso(), run_id),
            )
            return cursor.rowcount == 1

    def mark_row_running(self, run_id: str, row_index: int) -> bool:
        with self._transaction() as conn:
            now = _utc_now_iso()
            cursor = conn.execute(
                """
                UPDATE batch_questions SET status = 'running', updated_at = ?
                WHERE run_id = ? AND row_index = ? AND status = 'pending'
                """,
                (now, run_id, row_index),
            )
            if cursor.rowcount == 0:
                return False
            self._touch_run(conn, run_id, now)
            return True

    def complete_row(self, run_id: str, row_index: int, response_json: str) -> bool:
        with self._transaction() as conn:
            now = _utc_now_iso()
            cursor = conn.execute(
                """
                UPDATE batch_questions
                SET status = 'succeeded', response_json = ?, error = NULL,
                    updated_at = ?
                WHERE run_id = ? AND row_index = ? AND status = 'running'
                """,
                (response_json, now, run_id, row_index),
            )
            if cursor.rowcount == 0:
                return False
            self._touch_run(conn, run_id, now)
            return True

    def fail_row(self, run_id: str, row_index: int, error: str) -> bool:
        with self._transaction() as conn:
            now = _utc_now_iso()
            cursor = conn.execute(
                """
                UPDATE batch_questions
                SET status = 'failed', response_json = NULL, error = ?,
                    updated_at = ?
                WHERE run_id = ? AND row_index = ? AND status = 'running'
                """,
                (error, now, run_id, row_index),
            )
            if cursor.rowcount == 0:
                return False
            self._touch_run(conn, run_id, now)
            return True

    def finalize_run(self, run_id: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE batch_runs SET status = 'completed', updated_at = ?
                WHERE run_id = ? AND status = 'running'
                  AND NOT EXISTS (
                      SELECT 1 FROM batch_questions
                      WHERE run_id = ? AND status IN (?, ?)
                  )
                """,
                (_utc_now_iso(), run_id, run_id, *_ACTIVE_ROW_STATUSES),
            )

    def mark_run_interrupted(self, run_id: str) -> None:
        with self._transaction() as conn:
            now = _utc_now_iso()
            # 与 recover_after_restart 语义对称：先把卡在 running 的行回退为
            # pending，否则 resume 后 finalize_run 会因残留 running 行永远无法
            # 判定 completed，run 会卡死在 running。
            conn.execute(
                "UPDATE batch_questions SET status = 'pending', updated_at = ? "
                "WHERE run_id = ? AND status = 'running'",
                (now, run_id),
            )
            conn.execute(
                """
                UPDATE batch_runs SET status = 'interrupted', updated_at = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (now, run_id),
            )

    def retry_row(self, run_id: str, row_index: int) -> str:
        with self._transaction() as conn:
            run_status = self._run_status(conn, run_id)
            if run_status is None:
                return "run_not_found"
            # 执行中的 run 不允许重试：否则会把 run 改回 pending，绕过
            # delete_run 的「执行中禁止删除」防护，且 worker 结果会被丢弃。
            if run_status == "running":
                return "conflict"
            row_status = self._row_status(conn, run_id, row_index)
            if row_status is None:
                return "row_not_found"
            if row_status != "failed":
                return "conflict"
            now = _utc_now_iso()
            conn.execute(
                """
                UPDATE batch_questions
                SET status = 'pending', response_json = NULL, error = NULL,
                    bad_case_json = NULL, updated_at = ?
                WHERE run_id = ? AND row_index = ?
                """,
                (now, run_id, row_index),
            )
            conn.execute(
                "UPDATE batch_runs SET status = 'pending', updated_at = ? "
                "WHERE run_id = ?",
                (now, run_id),
            )
            return "ok"

    def resume_run(self, run_id: str) -> str:
        with self._transaction() as conn:
            run_status = self._run_status(conn, run_id)
            if run_status is None:
                return "run_not_found"
            if run_status != "interrupted":
                return "conflict"
            conn.execute(
                "UPDATE batch_runs SET status = 'pending', updated_at = ? "
                "WHERE run_id = ?",
                (_utc_now_iso(), run_id),
            )
            return "ok"

    def save_row_bad_case(
        self, run_id: str, row_index: int, bad_case_json: str
    ) -> str:
        with self._transaction() as conn:
            run_status = self._run_status(conn, run_id)
            if run_status is None:
                return "run_not_found"
            row_status = self._row_status(conn, run_id, row_index)
            if row_status is None:
                return "row_not_found"
            if row_status not in _TERMINAL_ROW_STATUSES:
                return "conflict"
            now = _utc_now_iso()
            conn.execute(
                """
                UPDATE batch_questions SET bad_case_json = ?, updated_at = ?
                WHERE run_id = ? AND row_index = ?
                """,
                (bad_case_json, now, run_id, row_index),
            )
            self._touch_run(conn, run_id, now)
            return "ok"

    def clear_row_bad_case(self, run_id: str, row_index: int) -> None:
        """清空某行的 bad case 缓存字段（bad-case 双写补偿用，best-effort）。"""
        with self._transaction() as conn:
            now = _utc_now_iso()
            conn.execute(
                """
                UPDATE batch_questions SET bad_case_json = NULL, updated_at = ?
                WHERE run_id = ? AND row_index = ?
                """,
                (now, run_id, row_index),
            )
            self._touch_run(conn, run_id, now)

    def delete_run(self, run_id: str) -> str:
        with self._transaction() as conn:
            run_status = self._run_status(conn, run_id)
            if run_status is None:
                return "run_not_found"
            if run_status == "running":
                return "conflict"
            conn.execute(
                "DELETE FROM batch_questions WHERE run_id = ?",
                (run_id,),
            )
            conn.execute(
                "DELETE FROM batch_runs WHERE run_id = ? AND status != 'running'",
                (run_id,),
            )
            return "ok"

    def recover_after_restart(self) -> None:
        """启动恢复：先把 running 行回退为 pending，再把活跃 run 标记为中断。"""
        with self._transaction() as conn:
            now = _utc_now_iso()
            conn.execute(
                "UPDATE batch_questions SET status = 'pending', updated_at = ? "
                "WHERE status = 'running'",
                (now,),
            )
            conn.execute(
                "UPDATE batch_runs SET status = 'interrupted', updated_at = ? "
                "WHERE status IN ('running', 'pending')",
                (now,),
            )

    # ---------------------------------------------------------------- 辅助

    @staticmethod
    def _run_status(conn: sqlite3.Connection, run_id: str) -> str | None:
        row = conn.execute(
            "SELECT status FROM batch_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return None if row is None else row["status"]

    @staticmethod
    def _row_status(
        conn: sqlite3.Connection, run_id: str, row_index: int
    ) -> str | None:
        row = conn.execute(
            "SELECT status FROM batch_questions WHERE run_id = ? AND row_index = ?",
            (run_id, row_index),
        ).fetchone()
        return None if row is None else row["status"]

    @staticmethod
    def _touch_run(conn: sqlite3.Connection, run_id: str, now: str) -> None:
        conn.execute(
            "UPDATE batch_runs SET updated_at = ? WHERE run_id = ?",
            (now, run_id),
        )

    @staticmethod
    def _listing_entry_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "title": row["title"],
            "status": row["status"],
            "source_label": row["source_label"],
            "source_format": row["source_format"],
            "question_total": int(row["question_total"]),
            "question_done": int(row["question_done"]),
            "question_failed": int(row["question_failed"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _question_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "row_index": row["row_index"],
            "query": row["query"],
            "input_answer": row["input_answer"],
            "top_n": row["top_n"],
            "top_k": row["top_k"],
            "status": row["status"],
            "response": _load_json_or_none(row["response_json"]),
            "error": row["error"],
            "bad_case": _load_json_or_none(row["bad_case_json"]),
            "updated_at": row["updated_at"],
        }


def _load_json_or_none(raw: str | None) -> Any:
    if raw is None:
        return None
    return json.loads(raw)
