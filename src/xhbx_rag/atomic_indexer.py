from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .chunk_io import load_chunks_jsonl
from .index_lock import _normalize_uri, collection_write_lock
from .milvus_store import MilvusChunkRecord


_RAW_ROW_STRING_FIELDS = (
    "chunk_id",
    "text",
    "text_hash",
    "case_name",
    "chunk_type",
    "stage",
    "scenario",
    "metadata_json",
    "citations_json",
)
_RAW_ROW_FIELDS = {*_RAW_ROW_STRING_FIELDS, "vector"}
_ROLLBACK_PENDING_DETAIL = "知识库回滚尚未完成，请稍后重试"


@dataclass(frozen=True)
class AtomicIndexResult:
    indexed: int
    vector_dim: int


class AtomicIndexError(RuntimeError):
    pass


class RollbackPendingError(AtomicIndexError):
    def __init__(self, journal_path: Path, detail: str) -> None:
        super().__init__(detail)
        self.journal_path = journal_path


class AtomicIndexer:
    def __init__(self, *, embedding_client: object, store: object) -> None:
        self.embedding_client = embedding_client
        self.store = store

    def commit(
        self,
        chunks_path: Path,
        *,
        journal_dir: Path,
        on_state: Callable[[str, Path], None] | None = None,
    ) -> AtomicIndexResult:
        chunks = load_chunks_jsonl(chunks_path)
        if not chunks:
            return AtomicIndexResult(indexed=0, vector_dim=0)

        chunk_ids = [chunk.chunk_id for chunk in chunks]
        if len(set(chunk_ids)) != len(chunk_ids):
            raise AtomicIndexError("待入库 chunk_id 存在重复")

        embed_documents = getattr(self.embedding_client, "embed_documents")
        vectors = embed_documents([chunk.text for chunk in chunks])
        vector_dim = _validate_vectors(vectors, len(chunks))
        rows = [
            MilvusChunkRecord.from_chunk(chunk, vector).to_row()
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]

        journal_path = journal_dir / "journal.json"
        snapshot_path = journal_dir / "snapshot.jsonl"
        prepared = False
        committed = False
        with _collection_write_context(self.store):
            collection_existed = bool(self.store.collection_exists())
            old_rows_by_id = self.store.fetch_raw_rows_by_ids(chunk_ids)
            old_rows = [old_rows_by_id[key] for key in sorted(old_rows_by_id)]
            journal = _new_journal(
                self.store,
                collection_existed=collection_existed,
                chunk_ids=chunk_ids,
            )
            _validate_snapshot(old_rows, journal)
            try:
                _write_snapshot(snapshot_path, old_rows)
                _write_journal(journal_path, journal)
                prepared = True
                _notify(on_state, "prepared", journal_path)

                self.store.ensure_collection(vector_dim)
                self.store.upsert_raw_rows(rows)
                self.store.flush()
                actual_ids = set(self.store.fetch_raw_rows_by_ids(chunk_ids))
                if actual_ids != set(chunk_ids):
                    raise AtomicIndexError("Milvus 写后 ID 集合校验失败")

                journal = {**journal, "state": "committed"}
                _write_journal(journal_path, journal)
                committed = True
                _notify(on_state, "committed", journal_path)
            except BaseException as exc:
                if prepared and not committed:
                    try:
                        self._rollback(
                            journal_path,
                            journal,
                            old_rows,
                        )
                    except RollbackPendingError as rollback_exc:
                        raise rollback_exc from exc
                elif not prepared and not journal_path.is_file():
                    _remove_durable(snapshot_path)
                raise

        return AtomicIndexResult(indexed=len(chunks), vector_dim=vector_dim)

    def recover(self, journal_path: Path) -> None:
        journal_path = Path(journal_path)
        with _collection_write_context(self.store):
            journal = _read_journal(journal_path)
            _validate_journal(journal, self.store)
            state = str(journal["state"])
            if state == "rolled_back":
                return

            chunk_ids = list(journal["chunk_ids"])
            if state == "committed":
                actual_ids = set(self.store.fetch_raw_rows_by_ids(chunk_ids))
                if actual_ids != set(chunk_ids):
                    raise AtomicIndexError("committed journal 的 ID 集合校验失败")
                return

            snapshot_path = _resolve_snapshot_path(journal_path, journal)
            old_rows = _read_snapshot(snapshot_path)
            _validate_snapshot(old_rows, journal)
            self._rollback(journal_path, journal, old_rows)

    def _rollback(
        self,
        journal_path: Path,
        journal: dict[str, Any],
        old_rows: list[dict[str, Any]],
    ) -> None:
        rolling_back = {**journal, "state": "rolling_back"}
        try:
            _write_journal(journal_path, rolling_back)
            if bool(journal["collection_existed"]):
                chunk_ids = list(journal["chunk_ids"])
                self.store.delete_by_ids(chunk_ids)
                self.store.upsert_raw_rows(old_rows)
                self.store.flush()
                restored = self.store.fetch_raw_rows_by_ids(chunk_ids)
                _verify_restored_rows(restored, old_rows)
            else:
                self.store.drop_collection()
                if self.store.collection_exists():
                    raise AtomicIndexError("回滚后 collection 仍然存在")

            rolled_back = {**journal, "state": "rolled_back"}
            _write_journal(journal_path, rolled_back)
        except BaseException as exc:
            if isinstance(exc, RollbackPendingError):
                raise
            raise RollbackPendingError(journal_path, _ROLLBACK_PENDING_DETAIL) from exc


def _validate_vectors(vectors: object, expected_count: int) -> int:
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        raise AtomicIndexError("embedding 数量与 chunk 数量不一致")
    if not vectors or not isinstance(vectors[0], list) or not vectors[0]:
        raise AtomicIndexError("embedding 返回空向量")
    vector_dim = len(vectors[0])
    if any(not isinstance(vector, list) or len(vector) != vector_dim for vector in vectors):
        raise AtomicIndexError("embedding 返回向量维度不一致")
    return vector_dim


def _collection_write_context(store: object):
    uri = getattr(store, "uri", None)
    collection_name = getattr(store, "collection_name", None)
    if uri is None or collection_name is None:
        return nullcontext()
    return collection_write_lock(str(uri), str(collection_name))


def _new_journal(
    store: object,
    *,
    collection_existed: bool,
    chunk_ids: list[str],
) -> dict[str, Any]:
    uri = _normalize_uri(str(getattr(store, "uri", "")))
    return {
        "version": 1,
        "collection": {
            "uri_sha256": hashlib.sha256(uri.encode("utf-8")).hexdigest(),
            "collection_name": str(getattr(store, "collection_name", "")),
        },
        "collection_existed": collection_existed,
        "chunk_ids": sorted(chunk_ids),
        "snapshot_path": "snapshot.jsonl",
        "state": "prepared",
    }


def _notify(
    on_state: Callable[[str, Path], None] | None,
    state: str,
    journal_path: Path,
) -> None:
    if on_state is not None:
        on_state(state, journal_path)


def _write_snapshot(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    payload = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    )
    _atomic_write_text(path, payload)


def _write_journal(path: Path, journal: dict[str, Any]) -> None:
    payload = json.dumps(journal, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _atomic_write_text(path, payload + "\n")


def _read_journal(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AtomicIndexError("commit journal 无法读取或格式无效") from exc
    if not isinstance(value, dict):
        raise AtomicIndexError("commit journal 必须是 JSON object")
    return value


def _validate_journal(journal: dict[str, Any], store: object) -> None:
    if journal.get("version") != 1:
        raise AtomicIndexError("不支持的 commit journal 版本")
    if journal.get("state") not in {
        "prepared",
        "committed",
        "rolling_back",
        "rolled_back",
    }:
        raise AtomicIndexError("commit journal state 无效")
    if not isinstance(journal.get("collection_existed"), bool):
        raise AtomicIndexError("commit journal collection_existed 无效")

    chunk_ids = journal.get("chunk_ids")
    if (
        not isinstance(chunk_ids, list)
        or any(not isinstance(item, str) or not item for item in chunk_ids)
        or len(set(chunk_ids)) != len(chunk_ids)
        or chunk_ids != sorted(chunk_ids)
    ):
        raise AtomicIndexError("commit journal chunk_ids 无效")

    collection = journal.get("collection")
    if not isinstance(collection, dict):
        raise AtomicIndexError("commit journal collection identity 无效")
    expected_uri_hash = hashlib.sha256(
        _normalize_uri(str(getattr(store, "uri", ""))).encode("utf-8")
    ).hexdigest()
    expected_name = str(getattr(store, "collection_name", ""))
    if not secrets.compare_digest(
        str(collection.get("uri_sha256", "")), expected_uri_hash
    ) or str(collection.get("collection_name", "")) != expected_name:
        raise AtomicIndexError("commit journal 与当前 collection 不匹配")

    if not isinstance(journal.get("snapshot_path"), str) or not journal[
        "snapshot_path"
    ]:
        raise AtomicIndexError("commit journal snapshot_path 无效")


def _resolve_snapshot_path(
    journal_path: Path, journal: dict[str, Any]
) -> Path:
    journal_dir = journal_path.resolve(strict=False).parent
    raw_path = Path(str(journal["snapshot_path"]))
    candidate = raw_path if raw_path.is_absolute() else journal_dir / raw_path
    resolved = candidate.resolve(strict=False)
    if resolved.parent != journal_dir:
        raise AtomicIndexError("commit journal snapshot_path 必须位于 journal 目录")
    return resolved


def _read_snapshot(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AtomicIndexError("rollback snapshot 无法读取") from exc

    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AtomicIndexError("rollback snapshot JSONL 格式无效") from exc
        if not isinstance(value, dict):
            raise AtomicIndexError("rollback snapshot 行必须是 JSON object")
        rows.append(value)
    return rows


def _validate_snapshot(
    rows: list[dict[str, Any]], journal: dict[str, Any]
) -> None:
    chunk_ids = set(journal["chunk_ids"])
    snapshot_ids: list[str] = []
    for row in rows:
        if not _RAW_ROW_FIELDS.issubset(row):
            raise AtomicIndexError("rollback snapshot 原始行字段不完整")
        if any(not isinstance(row[field], str) for field in _RAW_ROW_STRING_FIELDS):
            raise AtomicIndexError("rollback snapshot 原始行字符串字段无效")
        chunk_id = row.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id or chunk_id not in chunk_ids:
            raise AtomicIndexError("rollback snapshot chunk_id 无效")
        vector = row["vector"]
        if (
            not isinstance(vector, list)
            or not vector
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in vector
            )
        ):
            raise AtomicIndexError("rollback snapshot vector 无效")
        try:
            metadata = json.loads(row["metadata_json"])
            citations = json.loads(row["citations_json"])
        except json.JSONDecodeError as exc:
            raise AtomicIndexError("rollback snapshot JSON 字段无效") from exc
        if not isinstance(metadata, dict) or not isinstance(citations, list):
            raise AtomicIndexError("rollback snapshot JSON 字段类型无效")
        snapshot_ids.append(chunk_id)
    if len(set(snapshot_ids)) != len(snapshot_ids):
        raise AtomicIndexError("rollback snapshot chunk_id 重复")
    if not journal["collection_existed"] and rows:
        raise AtomicIndexError("原 collection 不存在时 snapshot 必须为空")


def _atomic_write_text(path: Path, payload: str) -> None:
    _ensure_durable_directory(path.parent)
    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    fd: int | None = None
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _ensure_durable_directory(path: Path) -> None:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent

    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in reversed(missing):
        _fsync_directory(directory)
        _fsync_directory(directory.parent)


def _remove_durable(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _verify_restored_rows(
    actual_by_id: dict[str, dict[str, Any]],
    expected_rows: list[dict[str, Any]],
) -> None:
    expected_by_id = {str(row["chunk_id"]): row for row in expected_rows}
    if set(actual_by_id) != set(expected_by_id):
        raise AtomicIndexError("回滚后 ID 集合校验失败")
    if any(actual_by_id[key] != row for key, row in expected_by_id.items()):
        raise AtomicIndexError("回滚后旧记录校验失败")
