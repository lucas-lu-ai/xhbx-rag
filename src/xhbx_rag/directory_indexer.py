from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ValidationError

from .index_lock import collection_write_lock
from .knowledge_domain import validate_domain_metadata
from .knowledge_normalizer import (
    UnsupportedKnowledgePath,
    discover_chunk_files,
    source_kind_for_path,
)
from .milvus_store import MilvusChunkRecord
from .models import RagChunk


_COLLECTION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,219}$")


class DirectoryIndexError(RuntimeError):
    pass


class _EmbeddingClient(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed one bounded batch of documents."""


class _DirectoryStore(Protocol):
    uri: str
    collection_name: str

    def collection_exists(self) -> bool: ...

    def ensure_collection(self, vector_dim: int) -> None: ...

    def upsert(self, records: list[MilvusChunkRecord]) -> None: ...

    def flush(self) -> None: ...

    def row_count(self) -> int: ...

    def fetch_all_chunk_ids(self) -> set[str]: ...

    def rename_collection(self, new_name: str) -> None: ...

    def drop_collection(self) -> None: ...


@dataclass(frozen=True)
class LoadedKnowledgeDirectory:
    files: list[Path]
    chunks: list[RagChunk]
    primary_domain_counts: dict[str, int]


@dataclass(frozen=True)
class DirectoryIndexResult:
    collection: str
    files: int
    indexed: int
    vector_dim: int
    primary_domain_counts: dict[str, int]


def validate_collection_name(name: str) -> str:
    normalized = str(name).strip()
    if not _COLLECTION_RE.fullmatch(normalized):
        raise DirectoryIndexError(
            "collection-name 必须以字母或下划线开头，且只包含字母、数字、下划线"
        )
    return normalized


def load_normalized_directory(root: Path) -> LoadedKnowledgeDirectory:
    root = Path(root)
    if not root.is_dir():
        raise DirectoryIndexError(f"规范化目录不存在: {root}")
    files = discover_chunk_files(root)
    if not files:
        raise DirectoryIndexError("规范化目录中未发现 chunk JSONL 文件")

    chunks: list[RagChunk] = []
    locations: dict[str, str] = {}
    errors: list[str] = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        try:
            expected_source_kind = source_kind_for_path(root, path)
        except UnsupportedKnowledgePath:
            errors.append(f"{relative}: 文件路径不受支持")
            continue
        loaded = list(_load_chunks_with_lines(path, relative, errors))
        if not loaded:
            errors.append(f"{relative}: 文件为空或没有有效 chunk")
            continue
        for line_no, chunk in loaded:
            location = f"{relative}:{line_no}"
            contract_errors = validate_domain_metadata(chunk.metadata)
            errors.extend(
                f"{location}: 一级标签合同无效: {message}"
                for message in contract_errors
            )
            if chunk.metadata.get("source_kind") != expected_source_kind:
                errors.append(
                    f"{location}: source_kind 与目录规则不一致，"
                    f"应为 {expected_source_kind}"
                )
            previous = locations.get(chunk.chunk_id)
            if previous is not None:
                errors.append(
                    f"{location}: 重复 chunk_id {chunk.chunk_id}，首次出现于 {previous}"
                )
            else:
                locations[chunk.chunk_id] = location
            chunks.append(chunk)

    if errors:
        raise DirectoryIndexError(
            "规范化目录预检失败：\n" + "\n".join(errors[:50])
        )
    if not chunks:
        raise DirectoryIndexError("规范化目录中没有可入库 chunk")
    counts = Counter(str(chunk.metadata["primary_domain"]) for chunk in chunks)
    return LoadedKnowledgeDirectory(
        files=files,
        chunks=chunks,
        primary_domain_counts=dict(sorted(counts.items())),
    )


def index_directory(
    chunks_dir: Path,
    embedding_client: _EmbeddingClient,
    store_factory: Callable[[str], _DirectoryStore],
    collection_name: str,
    *,
    batch_size: int = 64,
    mode: str = "rebuild",
) -> DirectoryIndexResult:
    if mode != "rebuild":
        raise DirectoryIndexError("index-dir 当前只支持 rebuild 模式")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise DirectoryIndexError("batch-size 必须是正整数")
    target_name = validate_collection_name(collection_name)
    loaded = load_normalized_directory(chunks_dir)

    staging_name = f"{target_name}__staging__{uuid4().hex[:8]}"
    backup_name = f"{target_name}__backup__{uuid4().hex[:8]}"
    staging = store_factory(staging_name)
    expected_ids = {chunk.chunk_id for chunk in loaded.chunks}
    vector_dim: int | None = None
    try:
        for batch in _batched(loaded.chunks, batch_size):
            try:
                vectors = embedding_client.embed_documents(
                    [chunk.text for chunk in batch]
                )
            except Exception as exc:
                raise DirectoryIndexError("向量生成失败") from exc
            vector_dim = _validate_vectors(
                vectors,
                expected_count=len(batch),
                expected_dim=vector_dim,
            )
            try:
                if not staging.collection_exists():
                    staging.ensure_collection(vector_dim)
                staging.upsert(
                    [
                        MilvusChunkRecord.from_chunk(chunk, vector)
                        for chunk, vector in zip(batch, vectors, strict=True)
                    ]
                )
            except Exception as exc:
                raise DirectoryIndexError("staging collection 写入失败") from exc

        if vector_dim is None:
            raise DirectoryIndexError("规范化目录中没有可生成向量的 chunk")
        try:
            staging.flush()
            actual_count = staging.row_count()
            actual_ids = staging.fetch_all_chunk_ids()
        except Exception as exc:
            raise DirectoryIndexError("staging collection 校验失败") from exc
        if actual_count != len(loaded.chunks):
            raise DirectoryIndexError(
                "staging row count 校验失败: "
                f"expected={len(loaded.chunks)} actual={actual_count}"
            )
        if actual_ids != expected_ids:
            raise DirectoryIndexError("staging chunk_id 集合校验失败")

        _swap_collections(
            store_factory,
            staging,
            target_name=target_name,
            backup_name=backup_name,
        )
    except Exception as exc:
        if staging.collection_name != target_name:
            try:
                staging.drop_collection()
            except Exception:
                pass
        if isinstance(exc, DirectoryIndexError):
            raise
        raise DirectoryIndexError("目录入库失败") from exc

    return DirectoryIndexResult(
        collection=target_name,
        files=len(loaded.files),
        indexed=len(loaded.chunks),
        vector_dim=vector_dim,
        primary_domain_counts=loaded.primary_domain_counts,
    )


def _swap_collections(
    store_factory: Callable[[str], _DirectoryStore],
    staging: _DirectoryStore,
    *,
    target_name: str,
    backup_name: str,
) -> None:
    target = store_factory(target_name)
    with _collection_write_context(target, target_name):
        target_existed = target.collection_exists()
        if target_existed:
            try:
                target.rename_collection(backup_name)
            except Exception as exc:
                raise DirectoryIndexError("collection 切换失败：旧库无法创建备份") from exc
        try:
            staging.rename_collection(target_name)
        except Exception as exc:
            if target_existed:
                try:
                    target.rename_collection(target_name)
                except Exception as restore_exc:
                    raise DirectoryIndexError(
                        "collection 切换失败且自动恢复失败；请将 "
                        f"{backup_name} rename 为 {target_name}"
                    ) from restore_exc
            raise DirectoryIndexError("collection 切换失败，旧目标库已保留") from exc
        if target_existed:
            try:
                target.drop_collection()
            except Exception as exc:
                raise DirectoryIndexError(
                    "新 collection 已切换，但旧备份清理失败；请手工删除 "
                    f"{backup_name}"
                ) from exc


def _collection_write_context(store: _DirectoryStore, target_name: str):
    uri = getattr(store, "uri", None)
    if uri is None:
        return nullcontext()
    return collection_write_lock(str(uri), target_name)


def _load_chunks_with_lines(
    path: Path,
    relative: str,
    errors: list[str],
) -> Iterator[tuple[int, RagChunk]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        errors.append(f"{relative}: 文件读取失败")
        return
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"{relative}:{line_no}: JSON 解析失败")
            continue
        try:
            yield line_no, RagChunk.model_validate(payload)
        except ValidationError:
            errors.append(f"{relative}:{line_no}: RagChunk 字段校验失败")


def _batched(items: Sequence[RagChunk], size: int) -> Iterator[list[RagChunk]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _validate_vectors(
    vectors: object,
    *,
    expected_count: int,
    expected_dim: int | None,
) -> int:
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        raise DirectoryIndexError("embedding 数量与 chunk 数量不一致")
    if not vectors or not isinstance(vectors[0], list) or not vectors[0]:
        raise DirectoryIndexError("embedding 返回空向量")
    vector_dim = len(vectors[0])
    if expected_dim is not None and vector_dim != expected_dim:
        raise DirectoryIndexError("embedding 返回向量维度不一致")
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != vector_dim:
            raise DirectoryIndexError("embedding 返回向量维度不一致")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in vector
        ):
            raise DirectoryIndexError("embedding 返回向量数值无效")
    return vector_dim
