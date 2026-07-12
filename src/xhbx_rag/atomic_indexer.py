from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from .chunk_io import chunk_text_hash, load_chunks_jsonl
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
_RAW_SCHEMA = tuple(sorted(_RAW_ROW_FIELDS))
_JOURNAL_FIELDS = {
    "version",
    "owner",
    "collection",
    "collection_existed",
    "chunk_ids",
    "snapshot_path",
    "snapshot_sha256",
    "snapshot_size",
    "snapshot_count",
    "vector_dim",
    "raw_schema",
    "state",
    "journal_sha256",
}
_ROLLBACK_PENDING_DETAIL = "知识库回滚尚未完成，请稍后重试"


@dataclass(frozen=True)
class AtomicIndexResult:
    indexed: int
    vector_dim: int


@dataclass(frozen=True)
class AtomicJournalIdentity:
    job_id: str
    attempt_no: int
    chunks_sha256: str


class AtomicIndexError(RuntimeError):
    pass


class UntrustedJournalError(AtomicIndexError):
    """journal/owner/snapshot 的确定性验证失败。"""


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
        identity: AtomicJournalIdentity | None = None,
        on_state: Callable[[str, Path], None] | None = None,
    ) -> AtomicIndexResult:
        _require_empty_journal_dir(journal_dir)
        try:
            chunks_digest = hashlib.sha256(Path(chunks_path).read_bytes()).hexdigest()
        except OSError as exc:
            raise AtomicIndexError("待入库 chunk 文件无效") from exc
        identity = identity or AtomicJournalIdentity("standalone", 1, chunks_digest)
        _validate_identity(identity)
        if not secrets.compare_digest(identity.chunks_sha256, chunks_digest):
            raise AtomicIndexError("待入库 chunk 文件与 owner identity 不匹配")
        try:
            chunks = load_chunks_jsonl(chunks_path)
        except Exception as exc:
            raise AtomicIndexError("待入库 chunk 文件无效") from exc
        if not chunks:
            raise AtomicIndexError("待入库 chunk 文件不能为空")

        chunk_ids = [chunk.chunk_id for chunk in chunks]
        if len(set(chunk_ids)) != len(chunk_ids):
            raise AtomicIndexError("待入库 chunk_id 存在重复")

        try:
            embed_documents = getattr(self.embedding_client, "embed_documents")
            vectors = embed_documents([chunk.text for chunk in chunks])
        except Exception as exc:
            raise AtomicIndexError("向量生成失败") from exc
        vector_dim = _validate_vectors(vectors, len(chunks))
        rows = [
            MilvusChunkRecord.from_chunk(chunk, vector).to_row()
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]

        journal_path = journal_dir / "journal.json"
        snapshot_path = journal_dir / "snapshot.jsonl"
        prepared = False
        committed = False
        try:
            with _collection_write_context(self.store):
                try:
                    _require_empty_journal_dir(journal_dir)
                    collection_existed = bool(self.store.collection_exists())
                    old_rows_by_id = self.store.fetch_raw_rows_by_ids(chunk_ids)
                    old_rows = [old_rows_by_id[key] for key in sorted(old_rows_by_id)]
                    snapshot_payload = _snapshot_payload(old_rows)
                    journal = _new_journal(
                        self.store,
                        collection_existed=collection_existed,
                        chunk_ids=chunk_ids,
                        snapshot_payload=snapshot_payload,
                        snapshot_count=len(old_rows),
                        vector_dim=vector_dim,
                        identity=identity,
                    )
                    _validate_snapshot(old_rows, journal)
                except AtomicIndexError:
                    raise
                except Exception as exc:
                    raise AtomicIndexError("知识库提交准备失败") from exc

                try:
                    _require_empty_journal_dir(journal_dir)
                    _write_snapshot(snapshot_path, snapshot_payload)
                    _write_journal(journal_path, journal, create=True)
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
                except Exception as exc:
                    if prepared and not committed:
                        try:
                            self._rollback(
                                journal_path,
                                journal,
                                old_rows,
                            )
                        except RollbackPendingError:
                            raise
                        if isinstance(exc, AtomicIndexError):
                            raise
                        raise AtomicIndexError("知识库写入失败，已完成回滚") from exc
                    if committed:
                        raise AtomicIndexError("知识库已提交，但状态同步失败") from exc
                    raise AtomicIndexError("知识库提交准备失败") from exc
        except AtomicIndexError:
            raise
        except Exception as exc:
            raise AtomicIndexError("知识库提交失败") from exc

        return AtomicIndexResult(indexed=len(chunks), vector_dim=vector_dim)

    def recover(
        self, journal_path: Path, *, expected_identity: AtomicJournalIdentity | None = None
    ) -> None:
        journal_path = Path(journal_path)
        try:
            with _collection_write_context(self.store):
                journal = _read_journal(journal_path)
                try:
                    _validate_journal(journal, self.store)
                    _validate_expected_identity(journal, expected_identity)
                except AtomicIndexError as exc:
                    raise UntrustedJournalError(str(exc)) from exc
                state = str(journal["state"])
                if state == "rolled_back":
                    return

                chunk_ids = list(journal["chunk_ids"])
                if state == "committed":
                    actual_ids = set(self.store.fetch_raw_rows_by_ids(chunk_ids))
                    if actual_ids != set(chunk_ids):
                        raise AtomicIndexError("committed journal 的 ID 集合校验失败")
                    return

                try:
                    snapshot_path = _resolve_snapshot_path(journal_path, journal)
                    old_rows = _read_snapshot(snapshot_path, journal)
                    _validate_snapshot(old_rows, journal)
                except AtomicIndexError as exc:
                    if str(exc) == "rollback snapshot 无法读取":
                        raise
                    raise UntrustedJournalError(str(exc)) from exc
                self._rollback(journal_path, journal, old_rows)
        except AtomicIndexError:
            raise
        except Exception as exc:
            raise AtomicIndexError("知识库恢复失败") from exc

    def inspect_journal_state(
        self,
        journal_path: Path,
        *,
        expected_identity: AtomicJournalIdentity | None = None,
    ) -> Literal["prepared", "committed", "rolling_back", "rolled_back"]:
        """只读校验恢复材料，并返回 durable journal 状态。"""
        journal_path = Path(journal_path)
        try:
            with _collection_write_context(self.store):
                journal = _read_journal(journal_path)
                try:
                    _validate_journal(journal, self.store)
                    _validate_expected_identity(journal, expected_identity)
                except AtomicIndexError as exc:
                    raise UntrustedJournalError(str(exc)) from exc
                state = str(journal["state"])
                if state == "committed":
                    chunk_ids = list(journal["chunk_ids"])
                    actual_ids = set(self.store.fetch_raw_rows_by_ids(chunk_ids))
                    if actual_ids != set(chunk_ids):
                        raise AtomicIndexError("committed journal 的 ID 集合校验失败")
                elif state in {"prepared", "rolling_back"}:
                    try:
                        snapshot_path = _resolve_snapshot_path(journal_path, journal)
                        old_rows = _read_snapshot(snapshot_path, journal)
                        _validate_snapshot(old_rows, journal)
                    except AtomicIndexError as exc:
                        if str(exc) == "rollback snapshot 无法读取":
                            raise
                        raise UntrustedJournalError(str(exc)) from exc
                return cast(
                    Literal["prepared", "committed", "rolling_back", "rolled_back"],
                    state,
                )
        except AtomicIndexError:
            raise
        except Exception as exc:
            raise AtomicIndexError("commit journal 状态检查失败") from exc

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
        except Exception as exc:
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
    if any(not _is_finite_number(value) for vector in vectors for value in vector):
        raise AtomicIndexError("embedding 返回向量数值无效")
    return vector_dim


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


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
    snapshot_payload: bytes,
    snapshot_count: int,
    vector_dim: int,
    identity: AtomicJournalIdentity,
) -> dict[str, Any]:
    uri = _normalize_uri(str(getattr(store, "uri", "")))
    return {
        "version": 2,
        "owner": {
            "job_id": identity.job_id,
            "attempt_no": identity.attempt_no,
            "chunks_sha256": identity.chunks_sha256,
        },
        "collection": {
            "uri_sha256": hashlib.sha256(uri.encode("utf-8")).hexdigest(),
            "collection_name": str(getattr(store, "collection_name", "")),
        },
        "collection_existed": collection_existed,
        "chunk_ids": sorted(chunk_ids),
        "snapshot_path": "snapshot.jsonl",
        "snapshot_sha256": hashlib.sha256(snapshot_payload).hexdigest(),
        "snapshot_size": len(snapshot_payload),
        "snapshot_count": snapshot_count,
        "vector_dim": vector_dim,
        "raw_schema": list(_RAW_SCHEMA),
        "state": "prepared",
    }


def _notify(
    on_state: Callable[[str, Path], None] | None,
    state: str,
    journal_path: Path,
) -> None:
    if on_state is not None:
        on_state(state, journal_path)


def _snapshot_payload(rows: list[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")


def _write_snapshot(path: Path, payload: bytes) -> None:
    _atomic_write_bytes(path, payload, replace=False)


def _write_journal(
    path: Path, journal: dict[str, Any], *, create: bool = False
) -> None:
    payload = _journal_payload(journal)
    _atomic_write_bytes(path, payload, replace=not create)


def _journal_payload(journal: dict[str, Any]) -> bytes:
    checked = _journal_with_checksum(journal)
    payload = json.dumps(
        checked, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return payload + b"\n"


def _journal_with_checksum(journal: dict[str, Any]) -> dict[str, Any]:
    core = {key: value for key, value in journal.items() if key != "journal_sha256"}
    payload = json.dumps(
        core, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {**core, "journal_sha256": hashlib.sha256(payload).hexdigest()}


def _read_journal(path: Path) -> dict[str, Any]:
    try:
        payload = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AtomicIndexError("commit journal 无法读取") from exc
    except UnicodeError as exc:
        raise UntrustedJournalError("commit journal UTF-8 无效") from exc
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise UntrustedJournalError("commit journal 格式无效") from exc
    if not isinstance(value, dict):
        raise UntrustedJournalError("commit journal 必须是 JSON object")
    return value


def _validate_journal(journal: dict[str, Any], store: object) -> None:
    if set(journal) != _JOURNAL_FIELDS:
        raise AtomicIndexError("commit journal 核心字段无效")
    expected_checksum = _journal_with_checksum(journal)["journal_sha256"]
    checksum = journal.get("journal_sha256")
    if not isinstance(checksum, str) or not secrets.compare_digest(
        checksum, expected_checksum
    ):
        raise AtomicIndexError("commit journal 自校验失败")
    version = journal.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 2:
        raise AtomicIndexError("不支持的 commit journal 版本")
    owner = journal.get("owner")
    if not isinstance(owner, dict) or set(owner) != {
        "job_id",
        "attempt_no",
        "chunks_sha256",
    }:
        raise AtomicIndexError("commit journal owner identity 无效")
    _validate_identity(
        AtomicJournalIdentity(
            owner.get("job_id"), owner.get("attempt_no"), owner.get("chunks_sha256")
        )
    )
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
        or not chunk_ids
        or any(not isinstance(item, str) or not item for item in chunk_ids)
        or len(set(chunk_ids)) != len(chunk_ids)
        or chunk_ids != sorted(chunk_ids)
    ):
        raise AtomicIndexError("commit journal chunk_ids 无效")

    collection = journal.get("collection")
    if not isinstance(collection, dict) or set(collection) != {
        "uri_sha256",
        "collection_name",
    }:
        raise AtomicIndexError("commit journal collection identity 无效")
    uri_hash = collection.get("uri_sha256")
    collection_name = collection.get("collection_name")
    if not _is_sha256(uri_hash) or not isinstance(collection_name, str):
        raise AtomicIndexError("commit journal collection identity 无效")
    expected_uri_hash = hashlib.sha256(
        _normalize_uri(str(getattr(store, "uri", ""))).encode("utf-8")
    ).hexdigest()
    expected_name = str(getattr(store, "collection_name", ""))
    if not secrets.compare_digest(
        uri_hash, expected_uri_hash
    ) or collection_name != expected_name:
        raise AtomicIndexError("commit journal 与当前 collection 不匹配")

    if not isinstance(journal.get("snapshot_path"), str) or not journal[
        "snapshot_path"
    ]:
        raise AtomicIndexError("commit journal snapshot_path 无效")
    if not _is_sha256(journal.get("snapshot_sha256")):
        raise AtomicIndexError("commit journal snapshot 摘要无效")
    for field in ("snapshot_size", "snapshot_count"):
        value = journal.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AtomicIndexError("commit journal snapshot 元数据无效")
    vector_dim = journal.get("vector_dim")
    if isinstance(vector_dim, bool) or not isinstance(vector_dim, int) or vector_dim <= 0:
        raise AtomicIndexError("commit journal vector_dim 无效")
    if journal.get("raw_schema") != list(_RAW_SCHEMA):
        raise AtomicIndexError("commit journal raw schema 无效")


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _validate_identity(identity: AtomicJournalIdentity) -> None:
    if (
        not isinstance(identity.job_id, str)
        or not identity.job_id
        or isinstance(identity.attempt_no, bool)
        or not isinstance(identity.attempt_no, int)
        or identity.attempt_no <= 0
        or not _is_sha256(identity.chunks_sha256)
    ):
        raise AtomicIndexError("commit journal owner identity 无效")


def _validate_expected_identity(
    journal: dict[str, Any], expected: AtomicJournalIdentity | None
) -> None:
    if expected is None:
        return
    _validate_identity(expected)
    owner = journal["owner"]
    if owner != {
        "job_id": expected.job_id,
        "attempt_no": expected.attempt_no,
        "chunks_sha256": expected.chunks_sha256,
    }:
        raise AtomicIndexError("commit journal owner identity 不匹配")


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


def _read_snapshot(path: Path, journal: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise AtomicIndexError("rollback snapshot 无法读取") from exc
    if len(payload) != journal["snapshot_size"] or not secrets.compare_digest(
        hashlib.sha256(payload).hexdigest(), journal["snapshot_sha256"]
    ):
        raise AtomicIndexError("rollback snapshot 完整性校验失败")
    if payload and not payload.endswith(b"\n"):
        raise AtomicIndexError("rollback snapshot JSONL 结尾无效")
    lines = [] if not payload else payload[:-1].split(b"\n")

    rows: list[dict[str, Any]] = []
    for raw_line in lines:
        if not raw_line.strip():
            raise AtomicIndexError("rollback snapshot 不允许空行")
        try:
            line = raw_line.decode("utf-8")
        except UnicodeError as exc:
            raise AtomicIndexError("rollback snapshot 编码无效") from exc
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
    if len(rows) != journal["snapshot_count"]:
        raise AtomicIndexError("rollback snapshot 行数无效")
    for row in rows:
        if set(row) != _RAW_ROW_FIELDS:
            raise AtomicIndexError("rollback snapshot 原始行字段集合无效")
        if any(not isinstance(row[field], str) for field in _RAW_ROW_STRING_FIELDS):
            raise AtomicIndexError("rollback snapshot 原始行字符串字段无效")
        chunk_id = row.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id or chunk_id not in chunk_ids:
            raise AtomicIndexError("rollback snapshot chunk_id 无效")
        vector = row["vector"]
        if (
            not isinstance(vector, list)
            or not vector
            or any(not _is_finite_number(value) for value in vector)
        ):
            raise AtomicIndexError("rollback snapshot vector 无效")
        if len(vector) != journal["vector_dim"]:
            raise AtomicIndexError("rollback snapshot vector 维度无效")
        if row["text_hash"] != chunk_text_hash(row["text"]):
            raise AtomicIndexError("rollback snapshot text_hash 无效")
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


def _atomic_write_bytes(path: Path, payload: bytes, *, replace: bool = True) -> None:
    _ensure_durable_directory(path.parent)
    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    fd: int | None = None
    temporary_created = False
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        temporary_created = True
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
            temporary_created = False
        else:
            os.link(temporary, path)
            temporary.unlink()
            temporary_created = False
        _fsync_directory(path.parent)
    finally:
        if fd is not None:
            os.close(fd)
        if temporary_created:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            else:
                _fsync_directory(path.parent)


def _require_empty_journal_dir(path: Path) -> None:
    if not path.exists():
        return
    if not path.is_dir():
        raise AtomicIndexError("回滚目录必须是空目录")
    try:
        has_entries = next(path.iterdir(), None) is not None
    except OSError as exc:
        raise AtomicIndexError("回滚目录无法安全检查") from exc
    if has_entries:
        raise AtomicIndexError("回滚目录必须是空目录")


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
