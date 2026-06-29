from __future__ import annotations

from typing import Protocol

from .milvus_store import MilvusSearchHit
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
) -> dict:
    understanding = query_agent.understand(query)
    if not understanding.needs_retrieval or understanding.intent == "out_of_scope":
        return {
            "original_query": query,
            "rewritten_query": understanding.rewritten_query,
            "intent": understanding.intent,
            "filters": understanding.filters.model_dump(mode="json"),
            "results": [],
            "reason": "query 不属于可检索范围",
        }

    rewritten_query = understanding.rewritten_query.strip()
    if not rewritten_query:
        raise ValueError("rewritten_query 不能为空")

    vector = embedding_client.embed_query(rewritten_query)
    filter_dict = _search_filters(understanding)
    hits = store.search(vector=vector, top_k=top_n, filters=filter_dict)
    reranked = reranker.rerank(
        rewritten_query,
        [hit.chunk.text for hit in hits],
        top_k=top_k,
    )
    return {
        "original_query": query,
        "rewritten_query": rewritten_query,
        "intent": understanding.intent,
        "filters": understanding.filters.model_dump(mode="json"),
        "results": [_serialize_hit(hits[item.index], item) for item in reranked],
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
