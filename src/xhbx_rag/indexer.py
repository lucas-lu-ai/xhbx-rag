from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .chunk_io import load_chunks_jsonl
from .milvus_store import MilvusChunkRecord
from .observability import TraceSink, emit_trace


class _EmbeddingClient(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed document texts."""


class _Store(Protocol):
    def ensure_collection(self, vector_dim: int) -> None:
        """Ensure target collection exists."""

    def upsert(self, records: list[MilvusChunkRecord]) -> None:
        """Upsert records."""


def index_chunks(
    chunks_path: Path,
    embedding_client: _EmbeddingClient,
    store: _Store,
    trace: TraceSink | None = None,
) -> int:
    chunks = load_chunks_jsonl(chunks_path)
    emit_trace(
        trace,
        "index.chunks_loaded",
        {"chunks_path": str(chunks_path), "chunk_count": len(chunks)},
    )
    if not chunks:
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
