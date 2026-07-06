from __future__ import annotations

from typing import Protocol

from .milvus_store import MilvusSearchHit
from .observability import TraceSink, emit_trace
from .query_understanding import QueryUnderstanding
from .rerank import RerankResult
from .tagging import infer_query_tags

# 标签加权是软信号：每命中一条查询标签路径加 0.1，封顶 ×1.3，
# 只调排序不做硬过滤，避免规则漏标把相关 chunk 挡在召回外。
_TAG_BOOST_PER_MATCH = 0.1
_TAG_BOOST_MATCH_CAP = 3


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
    vector_hits = store.search(vector=vector, top_k=top_n, filters=filter_dict)
    emit_trace(
        trace,
        "search.vector_searched",
        {
            "filters": filter_dict,
            "requested_top_n": top_n,
            "candidate_count": len(vector_hits),
            "candidates": [_serialize_candidate(hit) for hit in vector_hits],
        },
    )
    keyword_hits = _keyword_search_if_available(
        store,
        query=rewritten_query,
        top_k=top_n,
        filters=filter_dict,
    )
    if keyword_hits is not None:
        emit_trace(
            trace,
            "search.keyword_searched",
            {
                "filters": filter_dict,
                "requested_top_n": top_n,
                "candidate_count": len(keyword_hits),
                "candidates": [_serialize_candidate(hit) for hit in keyword_hits],
            },
        )
        # 先全量融合，标签加权后再截断，命中标签的候选才有机会挤进 top_n。
        candidates = _rrf_fuse(vector_hits, keyword_hits, limit=None)
        emit_trace(
            trace,
            "search.hybrid_fused",
            {
                "result_count": len(candidates[:top_n]),
                "candidates": [
                    _serialize_candidate(hit) for hit in candidates[:top_n]
                ],
            },
        )
    else:
        candidates = vector_hits
    query_tag_paths = infer_query_tags(rewritten_query)
    if query_tag_paths:
        candidates, boost_details = _apply_tag_boost(candidates, query_tag_paths)
        emit_trace(
            trace,
            "search.tag_boosted",
            {
                "query_tag_paths": query_tag_paths,
                "boosted_count": len(boost_details),
                "boosted": boost_details,
            },
        )
    hits = candidates[:top_n]
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
    if filters.chunk_types and understanding.intent != "general_sales_qa":
        result["chunk_types"] = filters.chunk_types
    if filters.stage:
        result["stage"] = filters.stage
    return result


def _keyword_search_if_available(
    store: _Store,
    *,
    query: str,
    top_k: int,
    filters: dict,
) -> list[MilvusSearchHit] | None:
    keyword_search = getattr(store, "keyword_search", None)
    if keyword_search is None:
        return None
    return keyword_search(query=query, top_k=top_k, filters=filters)


def _apply_tag_boost(
    hits: list[MilvusSearchHit],
    query_tag_paths: list[str],
) -> tuple[list[MilvusSearchHit], list[dict]]:
    query_paths = set(query_tag_paths)
    boosted_hits: list[MilvusSearchHit] = []
    details: list[dict] = []
    for hit in hits:
        chunk_paths = hit.chunk.metadata.get("tag_paths") or []
        matched = [str(path) for path in chunk_paths if str(path) in query_paths]
        if not matched:
            boosted_hits.append(hit)
            continue
        factor = 1 + _TAG_BOOST_PER_MATCH * min(len(matched), _TAG_BOOST_MATCH_CAP)
        boosted = MilvusSearchHit(chunk=hit.chunk, score=hit.score * factor)
        boosted_hits.append(boosted)
        details.append(
            {
                "chunk_id": hit.chunk.chunk_id,
                "matched_tag_paths": matched,
                "boost_factor": round(factor, 2),
                "fused_score": hit.score,
                "boosted_score": boosted.score,
            }
        )
    # sort 稳定：分数相同的候选保持加权前的先后顺序。
    boosted_hits.sort(key=lambda item: -item.score)
    return boosted_hits, details


def _rrf_fuse(
    vector_hits: list[MilvusSearchHit],
    keyword_hits: list[MilvusSearchHit],
    *,
    limit: int | None,
) -> list[MilvusSearchHit]:
    scores: dict[str, float] = {}
    hits_by_id: dict[str, MilvusSearchHit] = {}
    first_seen: dict[str, int] = {}
    seen_order = 0
    rrf_k = 60

    for hit_list in (vector_hits, keyword_hits):
        for rank, hit in enumerate(hit_list, start=1):
            chunk_id = hit.chunk.chunk_id
            if chunk_id not in hits_by_id:
                hits_by_id[chunk_id] = hit
                first_seen[chunk_id] = seen_order
                seen_order += 1
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (rrf_k + rank)

    ranked_ids = sorted(
        scores,
        key=lambda chunk_id: (-scores[chunk_id], first_seen[chunk_id]),
    )
    return [
        MilvusSearchHit(chunk=hits_by_id[chunk_id].chunk, score=scores[chunk_id])
        for chunk_id in ranked_ids[:limit]
    ]


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
