from pathlib import Path
from types import SimpleNamespace

import xhbx_rag.milvus_store as milvus_store
from xhbx_rag.milvus_store import MilvusChunkRecord, MilvusLiteStore
from xhbx_rag.models import EvidenceRef, RagChunk


def test_milvus_chunk_record_flattens_metadata_and_json_fields() -> None:
    chunk = RagChunk(
        chunk_id="chunk-1",
        chunk_type="script",
        text="话术文本",
        metadata={
            "case_name": "案例A",
            "stage": "售前",
            "scenario": "客户抗拒保险",
            "strategy_names": ["风险唤醒"],
            "knowledge_type": "场景话术",
            "tag_paths": ["销售技能/沟通谈判/保险理念沟通"],
        },
        citations=[EvidenceRef(section_name="第1节", quote="原文")],
        source_file="case.sales_insights.json",
    )

    record = MilvusChunkRecord.from_chunk(chunk, vector=[0.1, 0.2])
    row = record.to_row()

    assert row["chunk_id"] == "chunk-1"
    assert row["vector"] == [0.1, 0.2]
    assert row["case_name"] == "案例A"
    assert row["chunk_type"] == "script"
    assert row["stage"] == "售前"
    assert '"strategy_names"' in row["metadata_json"]
    assert '"knowledge_type": "场景话术"' in row["metadata_json"]
    assert "销售技能/沟通谈判/保险理念沟通" in row["metadata_json"]
    assert '"section_name"' in row["citations_json"]


def test_to_row_citations_drop_context_and_truncate_long_excerpt() -> None:
    chunk = RagChunk(
        chunk_id="chunk-1",
        chunk_type="script",
        text="话术文本",
        metadata={"case_name": "案例A"},
        citations=[
            EvidenceRef(
                section_name="第1节",
                quote="原文引述",
                context="很长的上下文" * 200,
                source_excerpt="超长摘录" * 500,
            )
        ],
        source_file="case.sales_insights.json",
    )

    row = MilvusChunkRecord.from_chunk(chunk, vector=[0.1, 0.2]).to_row()

    import json

    citations = json.loads(row["citations_json"])
    assert "context" not in citations[0]
    assert len(citations[0]["source_excerpt"]) <= 600
    assert citations[0]["quote"] == "原文引述"


def test_to_row_citations_json_never_exceeds_varchar_limit() -> None:
    refs = [
        EvidenceRef(
            section_name=f"第{i}节",
            quote=f"引述{i}" * 60,
            source_excerpt="摘录内容" * 150,
            source_path=f"案例A/第{i}节/文件{i}.txt",
        )
        for i in range(200)
    ]
    chunk = RagChunk(
        chunk_id="chunk-1",
        chunk_type="script",
        text="话术文本",
        metadata={"case_name": "案例A"},
        citations=refs,
        source_file="case.sales_insights.json",
    )

    row = MilvusChunkRecord.from_chunk(chunk, vector=[0.1, 0.2]).to_row()

    assert len(row["citations_json"].encode("utf-8")) <= 65535
    import json

    kept = json.loads(row["citations_json"])
    assert kept
    assert kept[0]["section_name"] == "第0节"


def test_create_milvus_store_uses_lite_path(monkeypatch) -> None:
    calls = []

    class FakeMilvusClient:
        def __init__(self, uri, **kwargs):
            calls.append({"uri": uri, "kwargs": kwargs})

    monkeypatch.setattr(milvus_store, "MilvusClient", FakeMilvusClient)

    store = milvus_store.create_milvus_store(
        SimpleNamespace(
            milvus_mode="lite",
            milvus_lite_path=Path(".local/milvus/xhbx_rag.db"),
            milvus_uri="http://127.0.0.1:19530",
            milvus_token="root:Milvus",
            milvus_collection="chunks",
        )
    )

    assert store.collection_name == "chunks"
    assert calls == [{"uri": ".local/milvus/xhbx_rag.db", "kwargs": {}}]


def test_create_milvus_store_uses_docker_uri_and_token(monkeypatch) -> None:
    calls = []

    class FakeMilvusClient:
        def __init__(self, uri, **kwargs):
            calls.append({"uri": uri, "kwargs": kwargs})

    monkeypatch.setattr(milvus_store, "MilvusClient", FakeMilvusClient)

    store = milvus_store.create_milvus_store(
        SimpleNamespace(
            milvus_mode="docker",
            milvus_lite_path=Path(".local/milvus/xhbx_rag.db"),
            milvus_uri="http://127.0.0.1:19530",
            milvus_token="root:Milvus",
            milvus_collection="chunks",
        )
    )

    assert store.collection_name == "chunks"
    assert calls == [{"uri": "http://127.0.0.1:19530", "kwargs": {"token": "root:Milvus"}}]


def test_milvus_lite_store_round_trips_records(tmp_path) -> None:
    store = MilvusLiteStore(
        db_path=tmp_path / "rag.db",
        collection_name="test_chunks",
    )
    chunk = RagChunk(
        chunk_id="chunk-1",
        chunk_type="script",
        text="客户不想聊保险时先聊家庭责任",
        metadata={"case_name": "案例A", "stage": "售前"},
        citations=[],
        source_file="case.sales_insights.json",
    )
    record = MilvusChunkRecord.from_chunk(chunk, vector=[0.1, 0.2, 0.3])

    store.ensure_collection(vector_dim=3)
    store.upsert([record])
    results = store.search(
        vector=[0.1, 0.2, 0.3],
        top_k=1,
        filters={"chunk_types": ["script"], "stage": "售前"},
    )

    assert len(results) == 1
    assert results[0].chunk.chunk_id == "chunk-1"
    assert results[0].chunk.text == "客户不想聊保险时先聊家庭责任"


def test_milvus_lite_store_loads_released_collection_before_search(tmp_path) -> None:
    store = MilvusLiteStore(
        db_path=tmp_path / "rag.db",
        collection_name="test_chunks",
    )
    chunk = RagChunk(
        chunk_id="chunk-1",
        chunk_type="script",
        text="客户不想聊保险时先聊家庭责任",
        metadata={"case_name": "案例A", "stage": "售前"},
        citations=[],
        source_file="case.sales_insights.json",
    )
    record = MilvusChunkRecord.from_chunk(chunk, vector=[0.1, 0.2, 0.3])

    store.ensure_collection(vector_dim=3)
    store.upsert([record])
    store.client.release_collection(store.collection_name)

    results = store.search(
        vector=[0.1, 0.2, 0.3],
        top_k=1,
        filters={"chunk_types": ["script"]},
    )

    assert len(results) == 1
    assert results[0].chunk.chunk_id == "chunk-1"


def test_milvus_lite_store_drop_collection_removes_existing_collection(tmp_path) -> None:
    store = MilvusLiteStore(
        db_path=tmp_path / "rag.db",
        collection_name="test_chunks",
    )

    store.ensure_collection(vector_dim=3)
    store.drop_collection()
    store.drop_collection()

    assert store.client.has_collection(store.collection_name) is False


def test_milvus_lite_store_keyword_search_ranks_exact_term_matches(tmp_path) -> None:
    store = MilvusLiteStore(
        db_path=tmp_path / "rag.db",
        collection_name="test_chunks",
    )
    chunks = [
        RagChunk(
            chunk_id="chunk-1",
            chunk_type="strategy",
            text="客户可以通过保单整理发现家庭保障缺口",
            metadata={"case_name": "案例A"},
            citations=[],
            source_file="case.sales_insights.json",
        ),
        RagChunk(
            chunk_id="chunk-2",
            chunk_type="script",
            text="客户提出每年交费不能超过80万时，可以使用预算释放与置换法",
            metadata={"case_name": "案例A", "stage": "异议处理"},
            citations=[],
            source_file="case.sales_insights.json",
        ),
    ]

    store.ensure_collection(vector_dim=3)
    store.upsert(
        [
            MilvusChunkRecord.from_chunk(chunks[0], vector=[0.1, 0.2, 0.3]),
            MilvusChunkRecord.from_chunk(chunks[1], vector=[0.2, 0.1, 0.3]),
        ]
    )

    results = store.keyword_search(
        query="预算释放 80万",
        top_k=1,
        filters={"chunk_types": ["script"]},
    )

    assert len(results) == 1
    assert results[0].chunk.chunk_id == "chunk-2"
    assert results[0].score > 0


def test_keyword_search_limits_candidate_payload_before_loading_details() -> None:
    class FakeMilvusClient:
        def __init__(self) -> None:
            self.query_calls = []

        def has_collection(self, collection_name):
            return True

        def load_collection(self, collection_name):
            pass

        def query(self, **kwargs):
            self.query_calls.append(kwargs)
            if len(self.query_calls) == 1:
                return [
                    {"chunk_id": "chunk-1", "text": "普通家庭保障配置"},
                    {"chunk_id": "chunk-2", "text": "客户预算不足时使用预算释放与置换法"},
                ]
            return [
                {
                    "chunk_id": "chunk-2",
                    "text": "客户预算不足时使用预算释放与置换法",
                    "case_name": "案例A",
                    "chunk_type": "script",
                    "stage": "异议处理",
                    "scenario": "",
                    "metadata_json": '{"case_name": "案例A", "stage": "异议处理"}',
                    "citations_json": "[]",
                }
            ]

    store = milvus_store.MilvusStore.__new__(milvus_store.MilvusStore)
    store.collection_name = "chunks"
    store.client = FakeMilvusClient()

    results = store.keyword_search(query="预算释放", top_k=20)

    assert [hit.chunk.chunk_id for hit in results] == ["chunk-2"]
    first_call, second_call = store.client.query_calls
    assert first_call["limit"] == 200
    assert first_call["output_fields"] == ["chunk_id", "text"]
    assert second_call["limit"] == 1
    assert second_call["filter"] == 'chunk_id in ["chunk-2"]'
