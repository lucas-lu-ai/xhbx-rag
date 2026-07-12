from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Literal, Protocol

from .chunk_io import load_chunks_jsonl
from .index_lock import collection_write_lock
from .milvus_store import MilvusChunkRecord
from .observability import TraceSink, emit_trace

IndexMode = Literal["incremental", "rebuild"]


class _EmbeddingClient(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed document texts."""


class _Store(Protocol):
    def ensure_collection(self, vector_dim: int) -> None:
        """Ensure target collection exists."""

    def drop_collection(self) -> None:
        """Drop target collection if it exists."""

    def upsert(self, records: list[MilvusChunkRecord]) -> None:
        """Upsert records."""


def _collection_write_context(store: _Store):
    uri = getattr(store, "uri", None)
    collection_name = getattr(store, "collection_name", None)
    if uri is None or collection_name is None:
        return nullcontext()
    return collection_write_lock(str(uri), str(collection_name))


def index_chunks(
    chunks_path: Path,
    embedding_client: _EmbeddingClient,
    store: _Store,
    trace: TraceSink | None = None,
    mode: IndexMode = "incremental",
) -> int:
    if mode not in ("incremental", "rebuild"):
        raise ValueError(f"未知 index mode: {mode}")
    chunks = load_chunks_jsonl(chunks_path)
    emit_trace(
        trace,
        "index.chunks_loaded",
        {"chunks_path": str(chunks_path), "chunk_count": len(chunks), "mode": mode},
    )
    if not chunks:
        if mode == "rebuild":
            with _collection_write_context(store):
                store.drop_collection()
                emit_trace(trace, "index.collection_dropped", {"mode": mode})
        return 0
    vectors = embedding_client.embed_documents([chunk.text for chunk in chunks])
    if len(vectors) != len(chunks):
        raise ValueError("embedding 数量与 chunk 数量不一致")
    vector_dim = len(vectors[0])
    if any(len(vector) != vector_dim for vector in vectors):
        raise ValueError("embedding 返回向量维度不一致")
    emit_trace(
        trace,
        "index.embedding_completed",
        {"chunk_count": len(chunks), "vector_count": len(vectors), "vector_dim": vector_dim},
    )
    with _collection_write_context(store):
        if mode == "rebuild":
            store.drop_collection()
            emit_trace(trace, "index.collection_dropped", {"mode": mode})
        store.ensure_collection(vector_dim)
        emit_trace(trace, "index.collection_ready", {"vector_dim": vector_dim})
        store.upsert(
            [
                MilvusChunkRecord.from_chunk(chunk, vector)
                for chunk, vector in zip(chunks, vectors, strict=True)
            ]
        )
        emit_trace(trace, "index.upsert_completed", {"upsert_count": len(chunks)})
    return len(chunks)
