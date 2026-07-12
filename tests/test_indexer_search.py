import json
from contextlib import contextmanager

import xhbx_rag.indexer as indexer
from xhbx_rag.indexer import index_chunks
from xhbx_rag.milvus_store import MilvusSearchHit
from xhbx_rag.models import RagChunk
from xhbx_rag.observability import MemoryTraceSink
from xhbx_rag.query_understanding import QueryFilters, QueryUnderstanding
from xhbx_rag.rerank import RerankResult
from xhbx_rag.search import search_evidence


class _FakeEmbedding:
    def __init__(self) -> None:
        self.documents: list[list[str]] = []
        self.queries: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.documents.append(texts)
        return [[float(index), 0.1] for index, _ in enumerate(texts, start=1)]

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.9, 0.1]


class _FakeStore:
    def __init__(self) -> None:
        self.ensured_dims: list[int] = []
        self.records = []
        self.drop_calls = 0
        self.operations: list[str] = []
        self.search_calls = []
        self.hits: list[MilvusSearchHit] = []

    def ensure_collection(self, vector_dim: int) -> None:
        self.ensured_dims.append(vector_dim)
        self.operations.append(f"ensure_collection:{vector_dim}")

    def upsert(self, records) -> None:
        self.records.extend(records)
        self.operations.append(f"upsert:{len(records)}")

    def drop_collection(self) -> None:
        self.drop_calls += 1
        self.operations.append("drop_collection")

    def search(self, vector: list[float], top_k: int, filters: dict):
        self.search_calls.append({"vector": vector, "top_k": top_k, "filters": filters})
        return self.hits


class _FakeHybridStore(_FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.keyword_calls = []
        self.keyword_hits: list[MilvusSearchHit] = []

    def keyword_search(self, query: str, top_k: int, filters: dict):
        self.keyword_calls.append({"query": query, "top_k": top_k, "filters": filters})
        return self.keyword_hits


class _FakeQueryAgent:
    def understand(self, query: str) -> QueryUnderstanding:
        assert query == "客户不想聊保险怎么开场？"
        return QueryUnderstanding(
            intent="script_search",
            rewritten_query="客户抗拒谈保险时如何开场",
            needs_retrieval=True,
            filters=QueryFilters(chunk_types=["script"], stage="售前"),
        )


class _FakeReranker:
    def rerank(self, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        assert query == "客户抗拒谈保险时如何开场"
        return [RerankResult(index=1, relevance_score=0.99, text=documents[1])]


class _CapturingReranker:
    def __init__(self) -> None:
        self.calls = []

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        self.calls.append({"query": query, "documents": documents, "top_k": top_k})
        return [
            RerankResult(index=index, relevance_score=1.0 - index * 0.1, text=document)
            for index, document in enumerate(documents[:top_k])
        ]


class _EmptyReranker:
    def rerank(self, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        return []


def _chunk(chunk_id: str, text: str) -> RagChunk:
    return RagChunk(
        chunk_id=chunk_id,
        chunk_type="script",
        text=text,
        metadata={"case_name": "案例A", "stage": "售前"},
        citations=[],
        source_file="case.sales_insights.json",
    )


def test_index_chunks_embeds_chunk_text_and_upserts_records(tmp_path) -> None:
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text(
        "\n".join(
            [
                json.dumps(_chunk("c1", "文本1").model_dump(mode="json"), ensure_ascii=False),
                json.dumps(_chunk("c2", "文本2").model_dump(mode="json"), ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    embedding = _FakeEmbedding()
    store = _FakeStore()

    count = index_chunks(chunks_path, embedding, store)

    assert count == 2
    assert embedding.documents == [["文本1", "文本2"]]
    assert store.ensured_dims == [2]
    assert [record.chunk.chunk_id for record in store.records] == ["c1", "c2"]
    assert store.drop_calls == 0


def test_index_chunks_rebuild_drops_collection_before_upsert(tmp_path) -> None:
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text(
        json.dumps(_chunk("c1", "文本1").model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    embedding = _FakeEmbedding()
    store = _FakeStore()

    count = index_chunks(chunks_path, embedding, store, mode="rebuild")

    assert count == 1
    assert embedding.documents == [["文本1"]]
    assert store.operations == ["drop_collection", "ensure_collection:2", "upsert:1"]


def test_index_chunks_locks_collection_writes(tmp_path, monkeypatch) -> None:
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text(
        json.dumps(_chunk("c1", "文本1").model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    events: list[str] = []

    class LockableStore(_FakeStore):
        uri = "db"
        collection_name = "chunks"

        def drop_collection(self) -> None:
            events.append("drop")
            super().drop_collection()

        def ensure_collection(self, vector_dim: int) -> None:
            events.append("ensure")
            super().ensure_collection(vector_dim)

        def upsert(self, records) -> None:
            events.append("upsert")
            super().upsert(records)

    @contextmanager
    def fake_lock(uri: str, collection_name: str):
        assert (uri, collection_name) == ("db", "chunks")
        events.append("lock")
        try:
            yield
        finally:
            events.append("unlock")

    monkeypatch.setattr(indexer, "collection_write_lock", fake_lock, raising=False)

    index_chunks(chunks_path, _FakeEmbedding(), LockableStore(), mode="rebuild")

    assert events == ["lock", "drop", "ensure", "upsert", "unlock"]


def test_index_chunks_emits_trace_events(tmp_path) -> None:
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text(
        json.dumps(_chunk("c1", "文本1").model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    trace = MemoryTraceSink()

    count = index_chunks(chunks_path, _FakeEmbedding(), _FakeStore(), trace=trace)

    assert count == 1
    assert [event.step for event in trace.events] == [
        "index.chunks_loaded",
        "index.embedding_completed",
        "index.collection_ready",
        "index.upsert_completed",
    ]
    assert trace.events[0].payload["chunk_count"] == 1
    assert trace.events[1].payload["vector_dim"] == 2


def test_search_evidence_embeds_rewritten_query_not_raw_query() -> None:
    embedding = _FakeEmbedding()
    store = _FakeStore()
    store.hits = [
        MilvusSearchHit(chunk=_chunk("c1", "不相关话术"), score=0.2),
        MilvusSearchHit(chunk=_chunk("c2", "客户抗拒时先聊家庭责任"), score=0.1),
    ]

    result = search_evidence(
        query="客户不想聊保险怎么开场？",
        query_agent=_FakeQueryAgent(),
        embedding_client=embedding,
        store=store,
        reranker=_FakeReranker(),
        top_n=20,
        top_k=1,
    )

    assert embedding.queries == ["客户抗拒谈保险时如何开场"]
    assert store.search_calls[0]["filters"] == {"chunk_types": ["script"], "stage": "售前"}
    assert result["original_query"] == "客户不想聊保险怎么开场？"
    assert result["rewritten_query"] == "客户抗拒谈保险时如何开场"
    assert result["results"][0]["chunk_id"] == "c2"
    assert result["results"][0]["rerank_score"] == 0.99


def test_search_evidence_ignores_chunk_type_filters_for_general_sales_qa() -> None:
    class _GeneralQaAgent:
        def understand(self, query: str) -> QueryUnderstanding:
            return QueryUnderstanding(
                intent="general_sales_qa",
                rewritten_query="保单整理对客户的作用和价值是什么？",
                needs_retrieval=True,
                filters=QueryFilters(chunk_types=["strategy", "script"]),
            )

    embedding = _FakeEmbedding()
    store = _FakeStore()

    search_evidence(
        query="保单整理对客户有什么作用？",
        query_agent=_GeneralQaAgent(),
        embedding_client=embedding,
        store=store,
        reranker=_EmptyReranker(),
        top_n=20,
        top_k=5,
    )

    assert store.search_calls[0]["filters"] == {}


def test_search_evidence_hybrid_retrieval_fuses_vector_and_keyword_hits() -> None:
    class _BudgetQueryAgent:
        def understand(self, query: str) -> QueryUnderstanding:
            return QueryUnderstanding(
                intent="objection_handling",
                rewritten_query="客户每年交费不能超过80万时如何处理预算异议？",
                needs_retrieval=True,
                filters=QueryFilters(chunk_types=["objection_handling", "script"]),
            )

    embedding = _FakeEmbedding()
    store = _FakeHybridStore()
    vector_hit = MilvusSearchHit(chunk=_chunk("vector", "泛泛讲预算异议"), score=0.7)
    keyword_hit = MilvusSearchHit(
        chunk=_chunk("keyword", "每年交费不能超过80万时使用预算释放与置换法"),
        score=3.2,
    )
    store.hits = [vector_hit]
    store.keyword_hits = [keyword_hit, vector_hit]
    reranker = _CapturingReranker()
    trace = MemoryTraceSink()

    result = search_evidence(
        query="客户说每年不能超过80万怎么办？",
        query_agent=_BudgetQueryAgent(),
        embedding_client=embedding,
        store=store,
        reranker=reranker,
        top_n=20,
        top_k=2,
        trace=trace,
    )

    assert store.search_calls[0]["top_k"] == 20
    assert store.keyword_calls[0] == {
        "query": "客户每年交费不能超过80万时如何处理预算异议？",
        "top_k": 20,
        "filters": {"chunk_types": ["objection_handling", "script"]},
    }
    assert reranker.calls[0]["documents"] == [
        "泛泛讲预算异议",
        "每年交费不能超过80万时使用预算释放与置换法",
    ]
    assert [item["chunk_id"] for item in result["results"]] == ["vector", "keyword"]
    assert [event.step for event in trace.events] == [
        "search.query_received",
        "search.query_understood",
        "search.query_embedded",
        "search.vector_searched",
        "search.keyword_searched",
        "search.hybrid_fused",
        "search.tag_boosted",
        "search.reranked",
        "search.completed",
    ]


def _tagged_chunk(chunk_id: str, text: str, tag_paths: list[str]) -> RagChunk:
    return RagChunk(
        chunk_id=chunk_id,
        chunk_type="script",
        text=text,
        metadata={"case_name": "案例A", "stage": "需求分析", "tag_paths": tag_paths},
        citations=[],
        source_file="case.sales_insights.json",
    )


def test_search_evidence_tag_boost_promotes_tag_matched_candidate() -> None:
    class _WealthQueryAgent:
        def understand(self, query: str) -> QueryUnderstanding:
            return QueryUnderstanding(
                intent="script_search",
                rewritten_query="高净值客户财富传承的话术",
                needs_retrieval=True,
                filters=QueryFilters(),
            )

    embedding = _FakeEmbedding()
    store = _FakeHybridStore()
    hit_a = MilvusSearchHit(chunk=_tagged_chunk("a", "开场寒暄", []), score=0.9)
    hit_b = MilvusSearchHit(chunk=_tagged_chunk("b", "讲产品组合", []), score=0.8)
    hit_c = MilvusSearchHit(
        chunk=_tagged_chunk(
            "c",
            "高净值客户传承安排讲解",
            ["客户画像/高净值客户", "客户需求/财富传承"],
        ),
        score=0.7,
    )
    store.hits = [hit_a, hit_b]
    store.keyword_hits = [hit_a, hit_c]
    reranker = _CapturingReranker()
    trace = MemoryTraceSink()

    result = search_evidence(
        query="高净值客户怎么聊财富传承？",
        query_agent=_WealthQueryAgent(),
        embedding_client=embedding,
        store=store,
        reranker=reranker,
        top_n=2,
        top_k=2,
        trace=trace,
    )

    # 无加权时 RRF 排序为 a > b > c，top_n=2 会截掉 c；
    # 标签命中 2 条路径后 c 的融合分 ×1.2 超过 b，挤进 rerank 名单。
    assert reranker.calls[0]["documents"] == ["开场寒暄", "高净值客户传承安排讲解"]
    assert [item["chunk_id"] for item in result["results"]] == ["a", "c"]

    # 命中信息随证据序列化输出，Web 层证据卡片直接展示。
    boosted_item = next(item for item in result["results"] if item["chunk_id"] == "c")
    assert boosted_item["matched_tag_paths"] == [
        "客户画像/高净值客户",
        "客户需求/财富传承",
    ]
    assert boosted_item["tag_boost_factor"] == 1.2
    plain_item = next(item for item in result["results"] if item["chunk_id"] == "a")
    assert plain_item["matched_tag_paths"] == []
    assert plain_item["tag_boost_factor"] == 1.0

    steps = [event.step for event in trace.events]
    assert steps.index("search.tag_boosted") == steps.index("search.hybrid_fused") + 1
    boost_event = trace.events[steps.index("search.tag_boosted")]
    assert "客户画像/高净值客户" in boost_event.payload["query_tag_paths"]
    assert boost_event.payload["boosted_count"] == 1
    assert boost_event.payload["boosted"][0]["chunk_id"] == "c"
    assert boost_event.payload["boosted"][0]["matched_tag_paths"] == [
        "客户画像/高净值客户",
        "客户需求/财富传承",
    ]
    assert boost_event.payload["boosted"][0]["boost_factor"] == 1.2


def test_search_evidence_skips_tag_boost_when_query_has_no_tags() -> None:
    embedding = _FakeEmbedding()
    store = _FakeHybridStore()
    hit = MilvusSearchHit(chunk=_chunk("c1", "客户抗拒时先聊家庭话题"), score=0.5)
    store.hits = [hit]
    store.keyword_hits = [hit]
    trace = MemoryTraceSink()

    search_evidence(
        query="客户不想聊保险怎么开场？",
        query_agent=_FakeQueryAgent(),
        embedding_client=embedding,
        store=store,
        reranker=_EmptyReranker(),
        top_n=20,
        top_k=1,
        trace=trace,
    )

    assert "search.tag_boosted" not in [event.step for event in trace.events]


def test_search_evidence_emits_step_trace_events() -> None:
    embedding = _FakeEmbedding()
    store = _FakeStore()
    store.hits = [
        MilvusSearchHit(chunk=_chunk("c1", "不相关话术"), score=0.2),
        MilvusSearchHit(chunk=_chunk("c2", "客户抗拒时先聊家庭责任"), score=0.1),
    ]
    trace = MemoryTraceSink()

    search_evidence(
        query="客户不想聊保险怎么开场？",
        query_agent=_FakeQueryAgent(),
        embedding_client=embedding,
        store=store,
        reranker=_FakeReranker(),
        top_n=20,
        top_k=1,
        trace=trace,
    )

    assert [event.step for event in trace.events] == [
        "search.query_received",
        "search.query_understood",
        "search.query_embedded",
        "search.vector_searched",
        "search.reranked",
        "search.completed",
    ]
    assert trace.events[1].payload["rewritten_query"] == "客户抗拒谈保险时如何开场"
    assert trace.events[3].payload["candidate_count"] == 2
    assert trace.events[4].payload["results"][0]["chunk_id"] == "c2"
