import json

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
        self.search_calls = []
        self.hits: list[MilvusSearchHit] = []

    def ensure_collection(self, vector_dim: int) -> None:
        self.ensured_dims.append(vector_dim)

    def upsert(self, records) -> None:
        self.records.extend(records)

    def search(self, vector: list[float], top_k: int, filters: dict):
        self.search_calls.append({"vector": vector, "top_k": top_k, "filters": filters})
        return self.hits


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
