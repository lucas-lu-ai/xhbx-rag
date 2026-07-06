from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pymilvus import DataType, MilvusClient

from .chunk_io import chunk_text_hash
from .models import EvidenceRef, RagChunk

logger = logging.getLogger(__name__)

_CITATION_EXCERPT_MAX_CHARS = 600
_CITATIONS_JSON_MAX_BYTES = 65_535
_CHUNK_OUTPUT_FIELDS = [
    "chunk_id",
    "text",
    "case_name",
    "chunk_type",
    "stage",
    "scenario",
    "metadata_json",
    "citations_json",
]
_KEYWORD_CANDIDATE_OUTPUT_FIELDS = ["chunk_id", "text"]
_KEYWORD_MIN_CANDIDATES = 50
_KEYWORD_MAX_CANDIDATES = 200
_KEYWORD_CANDIDATE_MULTIPLIER = 10


class MilvusStoreError(RuntimeError):
    """Raised when Milvus Lite operations fail."""


def _compact_citation(citation: EvidenceRef) -> dict[str, Any]:
    # context 不被检索/展示消费（完整版本在 chunks.jsonl / insights JSON 里），
    # 不入库，避免撞 Milvus VARCHAR 上限。
    data = citation.model_dump(mode="json", exclude={"context"})
    excerpt = data.get("source_excerpt", "")
    if isinstance(excerpt, str) and len(excerpt) > _CITATION_EXCERPT_MAX_CHARS:
        data["source_excerpt"] = excerpt[:_CITATION_EXCERPT_MAX_CHARS]
    return data


def _citations_json(chunk_id: str, citations: list[EvidenceRef]) -> str:
    compact = [_compact_citation(citation) for citation in citations]
    while compact:
        payload = json.dumps(compact, ensure_ascii=False)
        if len(payload.encode("utf-8")) <= _CITATIONS_JSON_MAX_BYTES:
            if len(compact) < len(citations):
                logger.warning(
                    "chunk %s 的 citations 超过 Milvus 字段上限，已丢弃尾部 %d/%d 条",
                    chunk_id,
                    len(citations) - len(compact),
                    len(citations),
                )
            return payload
        compact.pop()
    return "[]"


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
            "citations_json": _citations_json(self.chunk.chunk_id, self.chunk.citations),
        }


@dataclass(frozen=True)
class MilvusSearchHit:
    chunk: RagChunk
    score: float


class MilvusStore:
    def __init__(self, uri: str, collection_name: str, token: str = "") -> None:
        self.uri = uri
        self.collection_name = collection_name
        kwargs = {"token": token} if token else {}
        self.client = MilvusClient(uri, **kwargs)

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

    def drop_collection(self) -> None:
        if self.client.has_collection(self.collection_name):
            self.client.drop_collection(self.collection_name)

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
            output_fields=_CHUNK_OUTPUT_FIELDS,
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
        query_tokens = _bm25_tokens(query)
        if not query_tokens:
            return []
        rows = self.keyword_candidates(query_tokens, top_k=top_k, filters=filters)
        scored_rows = _bm25_score_rows(query_tokens, rows)[:top_k]
        ranked_ids = _ranked_chunk_ids(scored_rows)
        if not ranked_ids:
            return []
        rows_by_id = self.fetch_chunks_by_ids(ranked_ids)
        hits: list[MilvusSearchHit] = []
        for score, row in scored_rows:
            chunk_id = str(row.get("chunk_id", ""))
            detail_row = rows_by_id.get(chunk_id)
            if detail_row is not None:
                hits.append(
                    MilvusSearchHit(chunk=_chunk_from_entity(detail_row), score=score)
                )
        return hits

    def keyword_candidates(
        self,
        query_tokens: list[str],
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not query_tokens:
            return []
        if not self.client.has_collection(self.collection_name):
            raise MilvusStoreError("Milvus collection 不存在，请先运行 index")
        self.client.load_collection(self.collection_name)
        expr = _build_filter_expr(filters or {})
        return list(
            self.client.query(
                collection_name=self.collection_name,
                filter=expr,
                limit=_keyword_candidate_limit(top_k),
                output_fields=_KEYWORD_CANDIDATE_OUTPUT_FIELDS,
            )
        )

    def fetch_chunks_by_ids(self, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not chunk_ids:
            return {}
        detail_rows = self.client.query(
            collection_name=self.collection_name,
            filter=_chunk_id_filter_expr(chunk_ids),
            limit=len(chunk_ids),
            output_fields=_CHUNK_OUTPUT_FIELDS,
        )
        return {str(row.get("chunk_id", "")): row for row in detail_rows}

    def _collection_vector_dim(self) -> int:
        description = self.client.describe_collection(self.collection_name)
        for field in description.get("fields", []):
            if field.get("name") == "vector":
                dim = field.get("params", {}).get("dim")
                return int(dim)
        raise MilvusStoreError("Milvus collection 缺少 vector 字段")


class MultiCollectionStore:
    """跨多个 collection 的只读聚合检索视图。

    写入（ensure_collection / upsert / drop_collection）仍走各单库 store；
    聚合层只负责向量召回的分数合并与 BM25 候选合池统一打分。
    """

    def __init__(self, stores: list[MilvusStore]) -> None:
        if not stores:
            raise ValueError("MultiCollectionStore 需要至少一个 store")
        self.stores = list(stores)
        self._dims_validated = False

    def search(
        self,
        vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[MilvusSearchHit]:
        available = self._available_stores()
        self._validate_vector_dims(available)
        hit_lists = [
            store.search(vector=vector, top_k=top_k, filters=filters)
            for store in available
        ]
        return _merge_ranked_hit_lists(
            hit_lists,
            top_k,
            default_higher_is_better=not all(
                isinstance(store, MilvusLiteStore) for store in available
            ),
        )

    def keyword_search(
        self,
        query: str,
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[MilvusSearchHit]:
        if top_k <= 0:
            return []
        query_tokens = _bm25_tokens(query)
        if not query_tokens:
            return []
        available = self._available_stores()
        all_rows: list[dict[str, Any]] = []
        store_index_by_row_id: dict[int, int] = {}
        for store_index, store in enumerate(available):
            for row in store.keyword_candidates(query_tokens, top_k=top_k, filters=filters):
                store_index_by_row_id[id(row)] = store_index
                all_rows.append(row)
        # IDF 与平均文档长度必须在合并后的候选池上统一计算，
        # 各库独立打分的 BM25 分数不可比。
        scored_rows = _bm25_score_rows(query_tokens, all_rows)[:top_k]
        if not scored_rows:
            return []
        ids_by_store: dict[int, list[str]] = {}
        for _, row in scored_rows:
            store_index = store_index_by_row_id[id(row)]
            ids_by_store.setdefault(store_index, []).append(str(row.get("chunk_id", "")))
        details_by_store = {
            store_index: available[store_index].fetch_chunks_by_ids(chunk_ids)
            for store_index, chunk_ids in ids_by_store.items()
        }
        hits: list[MilvusSearchHit] = []
        for score, row in scored_rows:
            store_index = store_index_by_row_id[id(row)]
            chunk_id = str(row.get("chunk_id", ""))
            detail_row = details_by_store[store_index].get(chunk_id)
            if detail_row is not None:
                hits.append(
                    MilvusSearchHit(chunk=_chunk_from_entity(detail_row), score=score)
                )
        return hits

    def close(self) -> None:
        for store in self.stores:
            close = getattr(store.client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass

    def _available_stores(self) -> list[MilvusStore]:
        available = [
            store
            for store in self.stores
            if store.client.has_collection(store.collection_name)
        ]
        if not available:
            raise MilvusStoreError("Milvus collection 不存在，请先运行 index")
        if len(available) < len(self.stores):
            missing = [
                store.collection_name
                for store in self.stores
                if store not in available
            ]
            logger.warning("部分 Milvus collection 不存在，已降级跳过: %s", missing)
        return available

    def _validate_vector_dims(self, available: list[MilvusStore]) -> None:
        if self._dims_validated or len(available) < 2:
            return
        dims = {store.collection_name: store._collection_vector_dim() for store in available}
        if len(set(dims.values())) > 1:
            raise MilvusStoreError(f"Milvus collection 向量维度不一致: {dims}")
        self._dims_validated = True


def _merge_ranked_hit_lists(
    hit_lists: list[list[MilvusSearchHit]],
    top_k: int,
    default_higher_is_better: bool = True,
) -> list[MilvusSearchHit]:
    """按分数合并多个库的有序命中列表。

    同一 Milvus 实例、同一 metric 下各库分数可比，但方向不可写死：
    标准 server 的 COSINE distance 是相似度（越大越相似），Milvus Lite
    返回的是余弦距离（越小越相似）。每个库自身的返回顺序总是
    "最相似在前"，优先据此从分数序列推断方向；命中不足以推断时
    回退到调用方按部署类型给出的默认方向。
    """
    higher_is_better = _infer_score_direction(hit_lists, default_higher_is_better)
    merged = [hit for hits in hit_lists for hit in hits]
    merged.sort(key=lambda hit: hit.score, reverse=higher_is_better)
    return merged[:top_k]


def _infer_score_direction(
    hit_lists: list[list[MilvusSearchHit]], default: bool
) -> bool:
    for hits in hit_lists:
        scores = [hit.score for hit in hits]
        if len(scores) >= 2 and scores[0] != scores[-1]:
            return scores[0] > scores[-1]
    return default


class MilvusLiteStore(MilvusStore):
    def __init__(self, db_path: Path, collection_name: str) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(str(self.db_path), collection_name)


def create_milvus_store(config: Any, collection_name: str | None = None) -> MilvusStore:
    resolved_collection = collection_name or config.milvus_collection
    if config.milvus_mode == "lite":
        return MilvusLiteStore(
            db_path=config.milvus_lite_path,
            collection_name=resolved_collection,
        )
    return MilvusStore(
        uri=config.milvus_uri,
        collection_name=resolved_collection,
        token=config.milvus_token,
    )


def create_retrieval_store(config: Any) -> MultiCollectionStore:
    """构建生产读路径的聚合检索视图：案例库 + 课程库。"""
    return MultiCollectionStore(
        [
            create_milvus_store(config),
            create_milvus_store(config, collection_name=config.milvus_course_collection),
        ]
    )


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


def _keyword_candidate_limit(top_k: int) -> int:
    return min(
        _KEYWORD_MAX_CANDIDATES,
        max(_KEYWORD_MIN_CANDIDATES, top_k * _KEYWORD_CANDIDATE_MULTIPLIER),
    )


def _ranked_chunk_ids(scored_rows: list[tuple[float, dict[str, Any]]]) -> list[str]:
    chunk_ids: list[str] = []
    for _, row in scored_rows:
        chunk_id = str(row.get("chunk_id", "")).strip()
        if chunk_id and chunk_id not in chunk_ids:
            chunk_ids.append(chunk_id)
    return chunk_ids


def _chunk_id_filter_expr(chunk_ids: list[str]) -> str:
    quoted = ", ".join(f'"{_escape(chunk_id)}"' for chunk_id in chunk_ids)
    return f"chunk_id in [{quoted}]"


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
    return [
        MilvusSearchHit(chunk=_chunk_from_entity(row), score=score)
        for score, row in _bm25_score_rows(query_tokens, rows)
    ]


def _bm25_score_rows(
    query_tokens: list[str], rows: list[dict[str, Any]]
) -> list[tuple[float, dict[str, Any]]]:
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
    return scored


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
