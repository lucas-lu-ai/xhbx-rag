from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pymilvus import DataType, MilvusClient

from .chunk_io import chunk_text_hash
from .models import EvidenceRef, RagChunk


class MilvusStoreError(RuntimeError):
    """Raised when Milvus Lite operations fail."""


@dataclass(frozen=True)
class MilvusChunkRecord:
    chunk: RagChunk
    vector: list[float]
    text_hash: str

    @classmethod
    def from_chunk(cls, chunk: RagChunk, vector: list[float]) -> "MilvusChunkRecord":
        return cls(chunk=chunk, vector=vector, text_hash=chunk_text_hash(chunk.text))

    def to_row(self) -> dict[str, Any]:
        metadata = self.chunk.metadata
        return {
            "chunk_id": self.chunk.chunk_id,
            "vector": self.vector,
            "text": self.chunk.text,
            "text_hash": self.text_hash,
            "case_name": str(metadata.get("case_name", "")),
            "chunk_type": self.chunk.chunk_type,
            "stage": str(metadata.get("stage", "")),
            "scenario": str(metadata.get("scenario", "")),
            "metadata_json": json.dumps(metadata, ensure_ascii=False),
            "citations_json": json.dumps(
                [citation.model_dump(mode="json") for citation in self.chunk.citations],
                ensure_ascii=False,
            ),
        }


@dataclass(frozen=True)
class MilvusSearchHit:
    chunk: RagChunk
    score: float


class MilvusLiteStore:
    def __init__(self, db_path: Path, collection_name: str) -> None:
        self.db_path = db_path
        self.collection_name = collection_name
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.client = MilvusClient(str(self.db_path))

    def ensure_collection(self, vector_dim: int) -> None:
        if self.client.has_collection(self.collection_name):
            actual_dim = self._collection_vector_dim()
            if actual_dim != vector_dim:
                raise MilvusStoreError(
                    "Milvus collection 向量维度不一致: "
                    f"expected={actual_dim} actual={vector_dim}"
                )
            return

        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(
            field_name="chunk_id",
            datatype=DataType.VARCHAR,
            is_primary=True,
            max_length=512,
        )
        schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=vector_dim)
        schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=65535)
        schema.add_field(field_name="text_hash", datatype=DataType.VARCHAR, max_length=128)
        schema.add_field(field_name="case_name", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="chunk_type", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="stage", datatype=DataType.VARCHAR, max_length=256)
        schema.add_field(field_name="scenario", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="metadata_json", datatype=DataType.VARCHAR, max_length=65535)
        schema.add_field(field_name="citations_json", datatype=DataType.VARCHAR, max_length=65535)

        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_type="FLAT",
            metric_type="COSINE",
        )
        self.client.create_collection(
            self.collection_name,
            schema=schema,
            index_params=index_params,
        )

    def upsert(self, records: list[MilvusChunkRecord]) -> None:
        if not records:
            return
        self.client.upsert(
            collection_name=self.collection_name,
            data=[record.to_row() for record in records],
        )
        self.client.flush(self.collection_name)

    def search(
        self,
        vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[MilvusSearchHit]:
        if not self.client.has_collection(self.collection_name):
            raise MilvusStoreError("Milvus collection 不存在，请先运行 index")
        self.client.load_collection(self.collection_name)
        expr = _build_filter_expr(filters or {})
        results = self.client.search(
            collection_name=self.collection_name,
            data=[vector],
            filter=expr,
            limit=top_k,
            output_fields=[
                "chunk_id",
                "text",
                "case_name",
                "chunk_type",
                "stage",
                "scenario",
                "metadata_json",
                "citations_json",
            ],
        )
        hits: list[MilvusSearchHit] = []
        for item in results[0] if results else []:
            entity = item.get("entity", {})
            hits.append(
                MilvusSearchHit(
                    chunk=_chunk_from_entity(entity),
                    score=float(item.get("distance", 0.0)),
                )
            )
        return hits

    def keyword_search(
        self,
        query: str,
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[MilvusSearchHit]:
        if top_k <= 0:
            return []
        if not self.client.has_collection(self.collection_name):
            raise MilvusStoreError("Milvus collection 不存在，请先运行 index")
        query_tokens = _bm25_tokens(query)
        if not query_tokens:
            return []
        self.client.load_collection(self.collection_name)
        expr = _build_filter_expr(filters or {})
        rows = self.client.query(
            collection_name=self.collection_name,
            filter=expr,
            limit=max(1000, top_k * 20),
            output_fields=[
                "chunk_id",
                "text",
                "case_name",
                "chunk_type",
                "stage",
                "scenario",
                "metadata_json",
                "citations_json",
            ],
        )
        scored_hits = _bm25_rank(query_tokens, rows)
        return scored_hits[:top_k]

    def _collection_vector_dim(self) -> int:
        description = self.client.describe_collection(self.collection_name)
        for field in description.get("fields", []):
            if field.get("name") == "vector":
                dim = field.get("params", {}).get("dim")
                return int(dim)
        raise MilvusStoreError("Milvus collection 缺少 vector 字段")


def _build_filter_expr(filters: dict[str, Any]) -> str:
    parts: list[str] = []
    chunk_types = filters.get("chunk_types") or []
    if chunk_types:
        quoted = ", ".join(f'"{_escape(value)}"' for value in chunk_types)
        parts.append(f"chunk_type in [{quoted}]")
    if filters.get("stage"):
        parts.append(f'stage == "{_escape(str(filters["stage"]))}"')
    if filters.get("case_name"):
        parts.append(f'case_name == "{_escape(str(filters["case_name"]))}"')
    return " and ".join(parts)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _chunk_from_entity(entity: dict[str, Any]) -> RagChunk:
    metadata = json.loads(entity.get("metadata_json", "{}") or "{}")
    citations = [
        EvidenceRef.model_validate(citation)
        for citation in json.loads(entity.get("citations_json", "[]") or "[]")
    ]
    return RagChunk(
        chunk_id=entity["chunk_id"],
        chunk_type=entity["chunk_type"],
        text=entity["text"],
        metadata=metadata,
        citations=citations,
        source_file="case.sales_insights.json",
    )


def _bm25_rank(query_tokens: list[str], rows: list[dict[str, Any]]) -> list[MilvusSearchHit]:
    if not rows:
        return []
    tokenized_docs = [_bm25_tokens(str(row.get("text", ""))) for row in rows]
    doc_lengths = [len(tokens) for tokens in tokenized_docs]
    avg_doc_length = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 0.0
    if avg_doc_length == 0:
        return []

    document_frequency: Counter[str] = Counter()
    for tokens in tokenized_docs:
        document_frequency.update(set(tokens))

    query_counts = Counter(query_tokens)
    scored: list[tuple[float, dict[str, Any]]] = []
    total_docs = len(rows)
    k1 = 1.2
    b = 0.75
    for row, tokens, doc_length in zip(rows, tokenized_docs, doc_lengths, strict=True):
        token_counts = Counter(tokens)
        score = 0.0
        for token, query_frequency in query_counts.items():
            frequency = token_counts.get(token, 0)
            if frequency == 0:
                continue
            idf = math.log(
                ((total_docs - document_frequency[token] + 0.5)
                / (document_frequency[token] + 0.5))
                + 1
            )
            denominator = frequency + k1 * (1 - b + b * doc_length / avg_doc_length)
            score += idf * (frequency * (k1 + 1) / denominator) * query_frequency
        if score > 0:
            scored.append((score, row))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        MilvusSearchHit(chunk=_chunk_from_entity(row), score=score)
        for score, row in scored
    ]


def _bm25_tokens(text: str) -> list[str]:
    normalized = text.lower()
    tokens: list[str] = []
    for match in re.finditer(r"[\u4e00-\u9fff]+|[a-z0-9]+", normalized):
        segment = match.group(0)
        if re.fullmatch(r"[\u4e00-\u9fff]+", segment):
            tokens.extend(_cjk_ngrams(segment))
        else:
            tokens.append(segment)
    return tokens


def _cjk_ngrams(text: str) -> list[str]:
    tokens: list[str] = []
    max_size = min(4, len(text))
    for size in range(1, max_size + 1):
        tokens.extend(text[index : index + size] for index in range(len(text) - size + 1))
    return tokens
