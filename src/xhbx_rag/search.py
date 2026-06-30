from __future__ import annotations

from typing import Protocol

from .milvus_store import MilvusSearchHit
from .observability import TraceSink, emit_trace
from .query_understanding import QueryUnderstanding
from .rerank import RerankResult


class _QueryAgent(Protocol):
    def understand(self, query: str) -> QueryUnderstanding:
        """Understand and rewrite raw query."""


class _EmbeddingClient(Protocol):
    def embed_query(self, text: str) -> list[float]:
        """Embed rewritten query."""


class _Store(Protocol):
    def search(
        self,
        vector: list[float],
        top_k: int,
        filters: dict,
    ) -> list[MilvusSearchHit]:
        """Search vector store."""


class _Reranker(Protocol):
    def rerank(self, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        """Rerank candidate documents."""


def search_evidence(
    query: str,
    query_agent: _QueryAgent,
    embedding_client: _EmbeddingClient,
    store: _Store,
    reranker: _Reranker,
    top_n: int,
    top_k: int,
    trace: TraceSink | None = None,
) -> dict:
    emit_trace(
        trace,
        "search.query_received",
        {"query": query, "top_n": top_n, "top_k": top_k},
    )
    understanding = query_agent.understand(query)
    filters_payload = understanding.filters.model_dump(mode="json")
    emit_trace(
        trace,
        "search.query_understood",
        {
            "intent": understanding.intent,
            "rewritten_query": understanding.rewritten_query,
            "needs_retrieval": understanding.needs_retrieval,
            "filters": filters_payload,
        },
    )
    if not understanding.needs_retrieval or understanding.intent == "out_of_scope":
        emit_trace(
            trace,
            "search.skipped",
            {"reason": "query 不属于可检索范围", "intent": understanding.intent},
        )
        return {
            "original_query": query,
            "rewritten_query": understanding.rewritten_query,
            "intent": understanding.intent,
            "filters": filters_payload,
            "results": [],
            "reason": "query 不属于可检索范围",
        }

    rewritten_query = understanding.rewritten_query.strip()
    if not rewritten_query:
        raise ValueError("rewritten_query 不能为空")

    vector = embedding_client.embed_query(rewritten_query)
    emit_trace(
        trace,
        "search.query_embedded",
        {
            "input": rewritten_query,
            "vector_dim": len(vector),
            "vector_head": vector[:5],
        },
    )
    filter_dict = _search_filters(understanding)
    hits = store.search(vector=vector, top_k=top_n, filters=filter_dict)
    emit_trace(
        trace,
        "search.vector_searched",
        {
            "filters": filter_dict,
            "requested_top_n": top_n,
            "candidate_count": len(hits),
            "candidates": [_serialize_candidate(hit) for hit in hits],
        },
    )
    reranked = reranker.rerank(
        rewritten_query,
        [hit.chunk.text for hit in hits],
        top_k=top_k,
    )
    emit_trace(
        trace,
        "search.reranked",
        {
            "top_k": top_k,
            "result_count": len(reranked),
            "results": [
                _serialize_reranked_candidate(hits[item.index], item)
                for item in reranked
            ],
        },
    )
    results = [_serialize_hit(hits[item.index], item) for item in reranked]
    emit_trace(trace, "search.completed", {"result_count": len(results)})
    return {
        "original_query": query,
        "rewritten_query": rewritten_query,
        "intent": understanding.intent,
        "filters": filters_payload,
        "results": results,
    }


def _search_filters(understanding: QueryUnderstanding) -> dict:
    filters = understanding.filters
    result: dict = {}
    if filters.chunk_types:
        result["chunk_types"] = filters.chunk_types
    if filters.stage:
        result["stage"] = filters.stage
    return result


def _serialize_hit(hit: MilvusSearchHit, rerank: RerankResult) -> dict:
    return {
        "chunk_id": hit.chunk.chunk_id,
        "chunk_type": hit.chunk.chunk_type,
        "text": hit.chunk.text,
        "score": hit.score,
        "rerank_score": rerank.relevance_score,
        "metadata": hit.chunk.metadata,
        "citations": [
            citation.model_dump(mode="json") for citation in hit.chunk.citations
        ],
    }


def _serialize_candidate(hit: MilvusSearchHit) -> dict:
    return {
        "chunk_id": hit.chunk.chunk_id,
        "chunk_type": hit.chunk.chunk_type,
        "score": hit.score,
        "case_name": hit.chunk.metadata.get("case_name", ""),
        "stage": hit.chunk.metadata.get("stage", ""),
        "text_preview": _preview(hit.chunk.text),
    }


def _serialize_reranked_candidate(hit: MilvusSearchHit, rerank: RerankResult) -> dict:
    return {
        "chunk_id": hit.chunk.chunk_id,
        "chunk_type": hit.chunk.chunk_type,
        "vector_score": hit.score,
        "rerank_score": rerank.relevance_score,
        "case_name": hit.chunk.metadata.get("case_name", ""),
        "stage": hit.chunk.metadata.get("stage", ""),
        "text_preview": _preview(hit.chunk.text),
    }


def _preview(text: str, limit: int = 120) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "..."
