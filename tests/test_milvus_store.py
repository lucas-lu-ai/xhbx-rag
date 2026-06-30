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
    assert '"section_name"' in row["citations_json"]


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
