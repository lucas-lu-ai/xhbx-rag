from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from xhbx_rag.directory_indexer import (
    DirectoryIndexError,
    index_directory,
    load_normalized_directory,
    validate_collection_name,
)
from xhbx_rag.models import RagChunk


def _normalized_chunk(chunk_id: str, index: int = 0) -> RagChunk:
    return RagChunk(
        chunk_id=chunk_id,
        chunk_type="knowledge_entry",
        text=f"第 {index} 条产品培训知识",
        metadata={
            "title": "保险产品保障责任",
            "source_kind": "培训资料",
            "primary_domain": "产品知识",
            "domain_tags": ["产品知识"],
            "domain_tagging_method": "规则匹配",
            "domain_tagging_version": "2026-07-16",
        },
        citations=[],
        source_file="course.pptx",
    )


def _write_chunks(root: Path, chunks: list[RagChunk]) -> Path:
    path = root / "chunk" / "course.chunks.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(chunk.model_dump(mode="json"), ensure_ascii=False) + "\n"
            for chunk in chunks
        ),
        encoding="utf-8",
    )
    return path


class FakeEmbedding:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.fail_on_call = fail_on_call
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("embedding backend unavailable")
        return [[float(index), 0.5] for index, _ in enumerate(texts, start=1)]


class FakeBackend:
    def __init__(
        self,
        *,
        target_ids: set[str] | None = None,
        fail_staging_rename: bool = False,
    ) -> None:
        self.collections: dict[str, dict[str, dict]] = {}
        if target_ids is not None:
            self.collections["xhbx_knowledge_chunks"] = {
                chunk_id: {"chunk_id": chunk_id} for chunk_id in target_ids
            }
        self.fail_staging_rename = fail_staging_rename
        self.factory_calls: list[str] = []
        self.events: list[str] = []

    def factory(self, collection_name: str) -> "FakeStore":
        self.factory_calls.append(collection_name)
        return FakeStore(self, collection_name)


class FakeStore:
    uri = "memory://directory-indexer"

    def __init__(self, backend: FakeBackend, collection_name: str) -> None:
        self.backend = backend
        self.collection_name = collection_name

    def collection_exists(self) -> bool:
        return self.collection_name in self.backend.collections

    def ensure_collection(self, vector_dim: int) -> None:
        self.backend.events.append(f"ensure:{self.collection_name}:{vector_dim}")
        self.backend.collections.setdefault(self.collection_name, {})

    def upsert(self, records) -> None:
        rows = self.backend.collections[self.collection_name]
        for record in records:
            row = record.to_row()
            rows[row["chunk_id"]] = row
        self.backend.events.append(f"upsert:{self.collection_name}:{len(records)}")

    def flush(self) -> None:
        self.backend.events.append(f"flush:{self.collection_name}")

    def row_count(self) -> int:
        count = len(self.backend.collections.get(self.collection_name, {}))
        self.backend.events.append(f"row_count:{self.collection_name}:{count}")
        return count

    def fetch_all_chunk_ids(self) -> set[str]:
        result = set(self.backend.collections.get(self.collection_name, {}))
        self.backend.events.append(f"ids:{self.collection_name}:{len(result)}")
        return result

    def rename_collection(self, new_name: str) -> None:
        if (
            self.backend.fail_staging_rename
            and "__staging__" in self.collection_name
            and new_name == "xhbx_knowledge_chunks"
        ):
            raise RuntimeError("rename failed")
        self.backend.collections[new_name] = self.backend.collections.pop(
            self.collection_name
        )
        self.backend.events.append(f"rename:{self.collection_name}->{new_name}")
        self.collection_name = new_name

    def drop_collection(self) -> None:
        self.backend.collections.pop(self.collection_name, None)
        self.backend.events.append(f"drop:{self.collection_name}")


def test_invalid_metadata_fails_before_embedding_or_store_creation(
    tmp_path: Path,
) -> None:
    invalid = _normalized_chunk("bad")
    invalid = invalid.model_copy(update={"metadata": {}})
    _write_chunks(tmp_path, [invalid])
    embedding = FakeEmbedding()
    backend = FakeBackend()

    with pytest.raises(DirectoryIndexError, match="一级标签合同"):
        index_directory(
            tmp_path,
            embedding,
            backend.factory,
            "xhbx_knowledge_chunks",
            batch_size=2,
        )

    assert embedding.calls == []
    assert backend.factory_calls == []


def test_duplicate_ids_fail_during_full_directory_preflight(tmp_path: Path) -> None:
    first = _normalized_chunk("same", 1)
    second = _normalized_chunk("same", 2)
    _write_chunks(tmp_path, [first, second])

    with pytest.raises(DirectoryIndexError, match="重复 chunk_id"):
        load_normalized_directory(tmp_path)


def test_embedding_is_batched_and_staging_is_verified_before_swap(
    tmp_path: Path,
) -> None:
    _write_chunks(
        tmp_path,
        [_normalized_chunk(f"chunk-{index}", index) for index in range(5)],
    )
    embedding = FakeEmbedding()
    backend = FakeBackend(target_ids={"old"})

    result = index_directory(
        tmp_path,
        embedding,
        backend.factory,
        "xhbx_knowledge_chunks",
        batch_size=2,
    )

    assert [len(call) for call in embedding.calls] == [2, 2, 1]
    assert result.collection == "xhbx_knowledge_chunks"
    assert result.indexed == 5
    assert result.vector_dim == 2
    assert result.primary_domain_counts == {"产品知识": 5}
    assert set(backend.collections["xhbx_knowledge_chunks"]) == {
        f"chunk-{index}" for index in range(5)
    }
    assert not any("__staging__" in name for name in backend.collections)
    row_count_index = next(
        index
        for index, event in enumerate(backend.events)
        if event.startswith("row_count:")
    )
    rename_index = next(
        index
        for index, event in enumerate(backend.events)
        if "__staging__" in event and "->xhbx_knowledge_chunks" in event
    )
    assert row_count_index < rename_index


def test_embedding_failure_drops_staging_and_keeps_target(tmp_path: Path) -> None:
    _write_chunks(
        tmp_path,
        [_normalized_chunk(f"chunk-{index}", index) for index in range(3)],
    )
    embedding = FakeEmbedding(fail_on_call=2)
    backend = FakeBackend(target_ids={"old"})

    with pytest.raises(DirectoryIndexError, match="向量生成失败"):
        index_directory(
            tmp_path,
            embedding,
            backend.factory,
            "xhbx_knowledge_chunks",
            batch_size=2,
        )

    assert set(backend.collections["xhbx_knowledge_chunks"]) == {"old"}
    assert not any("__staging__" in name for name in backend.collections)


def test_staging_rename_failure_restores_old_target(tmp_path: Path) -> None:
    _write_chunks(tmp_path, [_normalized_chunk("new")])
    embedding = FakeEmbedding()
    backend = FakeBackend(target_ids={"old"}, fail_staging_rename=True)

    with pytest.raises(DirectoryIndexError, match="collection 切换失败"):
        index_directory(
            tmp_path,
            embedding,
            backend.factory,
            "xhbx_knowledge_chunks",
            batch_size=1,
        )

    assert set(backend.collections["xhbx_knowledge_chunks"]) == {"old"}
    assert not any("__backup__" in name for name in backend.collections)
    assert not any("__staging__" in name for name in backend.collections)


@pytest.mark.parametrize(
    "name",
    ["", "1bad", "bad-name", "含中文", "a" * 221],
)
def test_collection_name_uses_strict_whitelist(name: str) -> None:
    with pytest.raises(DirectoryIndexError, match="collection-name"):
        validate_collection_name(name)


def test_collection_name_accepts_safe_identifier() -> None:
    assert validate_collection_name("xhbx_knowledge_chunks") == (
        "xhbx_knowledge_chunks"
    )


def test_store_factory_protocol_is_callable() -> None:
    factory: Callable[[str], FakeStore] = FakeBackend().factory
    assert callable(factory)
