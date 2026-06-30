from __future__ import annotations

import json
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
            metadata = json.loads(entity.get("metadata_json", "{}") or "{}")
            citations = [
                EvidenceRef.model_validate(citation)
                for citation in json.loads(entity.get("citations_json", "[]") or "[]")
            ]
            chunk = RagChunk(
                chunk_id=entity["chunk_id"],
                chunk_type=entity["chunk_type"],
                text=entity["text"],
                metadata=metadata,
                citations=citations,
                source_file="case.sales_insights.json",
            )
            hits.append(MilvusSearchHit(chunk=chunk, score=float(item.get("distance", 0.0))))
        return hits

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
