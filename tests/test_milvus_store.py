from pathlib import Path
from types import SimpleNamespace

import pytest

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
            "source_kind": "绩优案例",
            "primary_domain": "销售技能",
            "domain_tags": ["销售技能"],
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
    assert row["source_kind"] == "绩优案例"
    assert row["primary_domain"] == "销售技能"
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


def test_create_retrieval_store_uses_only_unified_collection(monkeypatch) -> None:
    created = []

    def fake_create_milvus_store(config, collection_name=None):
        resolved = collection_name or config.milvus_collection
        created.append(resolved)
        return SimpleNamespace(
            collection_name=resolved,
            client=SimpleNamespace(has_collection=lambda name: True),
        )

    monkeypatch.setattr(milvus_store, "create_milvus_store", fake_create_milvus_store)

    store = milvus_store.create_retrieval_store(
        SimpleNamespace(
            milvus_collection="case_chunks",
            milvus_course_collection="course_chunks",
        ),
        collection_names=["course_chunks", "case_chunks", "course_chunks"],
    )

    assert created == ["case_chunks"]
    assert [item.collection_name for item in store.stores] == ["case_chunks"]


def test_configured_collection_names_excludes_legacy_course_collection() -> None:
    config = SimpleNamespace(
        milvus_collection="xhbx_knowledge_chunks",
        milvus_course_collection="xhbx_course_chunks",
    )

    assert milvus_store.configured_collection_names(config) == [
        "xhbx_knowledge_chunks"
    ]


def test_filter_expr_supports_source_kinds_and_primary_domains() -> None:
    expression = milvus_store._build_filter_expr(
        {
            "source_kinds": ["培训资料"],
            "primary_domains": ["产品知识", "合规与风控"],
        }
    )

    assert expression == (
        'source_kind in ["培训资料"] and '
        'primary_domain in ["产品知识", "合规与风控"]'
    )


def test_milvus_lite_store_round_trips_records(tmp_path) -> None:
    store = MilvusLiteStore(
        db_path=tmp_path / "rag.db",
        collection_name="test_chunks",
    )
    chunk = RagChunk(
        chunk_id="chunk-1",
        chunk_type="script",
        text="客户不想聊保险时先聊家庭责任",
        metadata={
            "case_name": "案例A",
            "stage": "售前",
            "source_kind": "绩优案例",
            "primary_domain": "销售技能",
            "domain_tags": ["销售技能"],
        },
        citations=[],
        source_file="case.sales_insights.json",
    )
    record = MilvusChunkRecord.from_chunk(chunk, vector=[0.1, 0.2, 0.3])

    store.ensure_collection(vector_dim=3)
    store.upsert([record])
    results = store.search(
        vector=[0.1, 0.2, 0.3],
        top_k=1,
        filters={
            "chunk_types": ["script"],
            "stage": "售前",
            "source_kinds": ["绩优案例"],
            "primary_domains": ["销售技能"],
        },
    )

    assert len(results) == 1
    assert results[0].chunk.chunk_id == "chunk-1"
    assert results[0].chunk.text == "客户不想聊保险时先聊家庭责任"


def test_milvus_lite_store_counts_ids_and_renames_collection(tmp_path) -> None:
    store = MilvusLiteStore(
        db_path=tmp_path / "rag.db",
        collection_name="staging_chunks",
    )
    chunks = [
        RagChunk(
            chunk_id=f"chunk-{index}",
            chunk_type="knowledge_entry",
            text=f"培训知识 {index}",
            metadata={
                "source_kind": "培训资料",
                "primary_domain": "产品知识",
                "domain_tags": ["产品知识"],
            },
            citations=[],
            source_file="course.pptx",
        )
        for index in range(3)
    ]
    store.ensure_collection(vector_dim=2)
    store.upsert(
        [
            MilvusChunkRecord.from_chunk(chunk, [float(index), 0.1])
            for index, chunk in enumerate(chunks)
        ]
    )

    assert store.row_count() == 3
    assert store.fetch_all_chunk_ids(batch_size=2) == {
        "chunk-0",
        "chunk-1",
        "chunk-2",
    }

    store.rename_collection("knowledge_chunks")

    assert store.collection_name == "knowledge_chunks"
    assert store.client.has_collection("knowledge_chunks") is True
    assert store.client.has_collection("staging_chunks") is False


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


def test_store_flushes_existing_collection() -> None:
    flush_calls: list[str] = []
    store = milvus_store.MilvusStore.__new__(milvus_store.MilvusStore)
    store.collection_name = "chunks"
    store.client = SimpleNamespace(
        has_collection=lambda collection_name: collection_name == "chunks",
        flush=lambda collection_name: flush_calls.append(collection_name),
    )

    store.flush()

    assert flush_calls == ["chunks"]


def test_store_flush_skips_missing_collection() -> None:
    flush_calls: list[str] = []
    store = milvus_store.MilvusStore.__new__(milvus_store.MilvusStore)
    store.collection_name = "missing"
    store.client = SimpleNamespace(
        has_collection=lambda _collection_name: False,
        flush=lambda collection_name: flush_calls.append(collection_name),
    )

    store.flush()

    assert flush_calls == []


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


def _lite_store_with_chunks(tmp_path, name: str, chunks: list[tuple[RagChunk, list[float]]]):
    # 两个 collection 共存于同一个 Milvus 实例（与 lite/docker 生产拓扑一致）
    store = MilvusLiteStore(db_path=tmp_path / "rag.db", collection_name=name)
    store.ensure_collection(vector_dim=len(chunks[0][1]))
    store.upsert([MilvusChunkRecord.from_chunk(chunk, vector) for chunk, vector in chunks])
    return store


def _chunk(chunk_id: str, text: str, chunk_type: str = "strategy") -> RagChunk:
    return RagChunk(
        chunk_id=chunk_id,
        chunk_type=chunk_type,
        text=text,
        metadata={"case_name": "案例A"},
        citations=[],
        source_file="demo",
    )


def test_multi_collection_store_merges_vector_hits_by_score(tmp_path) -> None:
    case_store = _lite_store_with_chunks(
        tmp_path, "case_chunks", [(_chunk("case-1", "保单整理发现缺口"), [1.0, 0.0, 0.0])]
    )
    course_store = _lite_store_with_chunks(
        tmp_path,
        "course_chunks",
        [(_chunk("course-1", "促成课程讲义", "training_course"), [0.9, 0.1, 0.0])],
    )
    store = milvus_store.MultiCollectionStore([case_store, course_store])

    hits = store.search(vector=[1.0, 0.0, 0.0], top_k=2, filters=None)

    # Milvus Lite 的 COSINE distance 越小越相似，聚合层需自适应方向：
    # case-1 与查询向量完全同向，必须排在 course-1 之前。
    assert [hit.chunk.chunk_id for hit in hits] == ["case-1", "course-1"]

    top1 = store.search(vector=[1.0, 0.0, 0.0], top_k=1, filters=None)
    assert [hit.chunk.chunk_id for hit in top1] == ["case-1"]


def test_multi_collection_store_keyword_search_scores_pooled_candidates(tmp_path) -> None:
    # “保单整理”在案例库出现于全部文档、课程库只出现一次；
    # 若各库独立打分，case 库中该词 IDF≈0，case-hit 会被埋没；
    # 合池统一打分后包含目标词的文档应稳定进入结果。
    case_store = _lite_store_with_chunks(
        tmp_path,
        "case_chunks",
        [
            (_chunk("case-1", "保单整理发现家庭保障缺口"), [1.0, 0.0, 0.0]),
            (_chunk("case-2", "保单整理是重要动作"), [0.9, 0.1, 0.0]),
        ],
    )
    course_store = _lite_store_with_chunks(
        tmp_path,
        "course_chunks",
        [
            (_chunk("course-1", "促成课程讲义与训练通关", "training_course"), [0.8, 0.2, 0.0]),
            (_chunk("course-2", "保单整理课程实操", "training_course"), [0.7, 0.3, 0.0]),
        ],
    )
    store = milvus_store.MultiCollectionStore([case_store, course_store])

    hits = store.keyword_search(query="保单整理", top_k=3)

    hit_ids = {hit.chunk.chunk_id for hit in hits}
    assert "course-2" in hit_ids
    assert hit_ids <= {"case-1", "case-2", "course-2"}
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)


def test_multi_collection_store_degrades_when_one_collection_missing(tmp_path) -> None:
    case_store = _lite_store_with_chunks(
        tmp_path, "case_chunks", [(_chunk("case-1", "保单整理发现缺口"), [1.0, 0.0, 0.0])]
    )
    empty_store = MilvusLiteStore(
        db_path=tmp_path / "rag.db", collection_name="course_chunks"
    )
    store = milvus_store.MultiCollectionStore([case_store, empty_store])

    vector_hits = store.search(vector=[1.0, 0.0, 0.0], top_k=2, filters=None)
    keyword_hits = store.keyword_search(query="保单整理", top_k=2)

    assert [hit.chunk.chunk_id for hit in vector_hits] == ["case-1"]
    assert [hit.chunk.chunk_id for hit in keyword_hits] == ["case-1"]


def test_multi_collection_store_raises_when_all_collections_missing(tmp_path) -> None:
    empty_a = MilvusLiteStore(db_path=tmp_path / "rag.db", collection_name="a_chunks")
    empty_b = MilvusLiteStore(db_path=tmp_path / "rag.db", collection_name="b_chunks")
    store = milvus_store.MultiCollectionStore([empty_a, empty_b])

    with pytest.raises(milvus_store.MilvusStoreError):
        store.search(vector=[1.0, 0.0, 0.0], top_k=1, filters=None)


def test_multi_collection_store_rejects_mismatched_vector_dims(tmp_path) -> None:
    case_store = _lite_store_with_chunks(
        tmp_path, "case_chunks", [(_chunk("case-1", "保单整理发现缺口"), [1.0, 0.0, 0.0])]
    )
    course_store = _lite_store_with_chunks(
        tmp_path,
        "course_chunks",
        [(_chunk("course-1", "促成课程讲义", "training_course"), [0.9, 0.1])],
    )
    store = milvus_store.MultiCollectionStore([case_store, course_store])

    with pytest.raises(milvus_store.MilvusStoreError, match="维度"):
        store.search(vector=[1.0, 0.0, 0.0], top_k=2, filters=None)


def test_keyword_candidates_and_fetch_chunks_by_ids(tmp_path) -> None:
    store = MilvusLiteStore(
        db_path=tmp_path / "rag.db",
        collection_name="test_chunks",
    )
    chunk = RagChunk(
        chunk_id="chunk-1",
        chunk_type="strategy",
        text="客户可以通过保单整理发现家庭保障缺口",
        metadata={"case_name": "案例A"},
        citations=[],
        source_file="case.sales_insights.json",
    )
    store.ensure_collection(vector_dim=3)
    store.upsert([MilvusChunkRecord.from_chunk(chunk, vector=[0.1, 0.2, 0.3])])

    candidates = store.keyword_candidates(
        query_tokens=milvus_store._bm25_tokens("保单整理"),
        top_k=5,
        filters=None,
    )

    assert [row["chunk_id"] for row in candidates] == ["chunk-1"]
    assert set(candidates[0]) >= {"chunk_id", "text"}

    rows_by_id = store.fetch_chunks_by_ids(["chunk-1", "chunk-missing"])

    assert set(rows_by_id) == {"chunk-1"}
    assert rows_by_id["chunk-1"]["chunk_type"] == "strategy"


def test_milvus_store_lists_distinct_filter_options(tmp_path) -> None:
    store = MilvusLiteStore(
        db_path=tmp_path / "rag.db",
        collection_name="test_chunks",
    )
    chunks = [
        RagChunk(
            chunk_id="chunk-1",
            chunk_type="script",
            text="售前话术",
            metadata={"case_name": "案例B", "stage": "售前"},
            citations=[],
            source_file="case.sales_insights.json",
        ),
        RagChunk(
            chunk_id="chunk-2",
            chunk_type="script",
            text="重复类型与案例",
            metadata={"case_name": "案例B", "stage": "售前"},
            citations=[],
            source_file="case.sales_insights.json",
        ),
        RagChunk(
            chunk_id="chunk-3",
            chunk_type="objection_handling",
            text="异议处理",
            metadata={"case_name": "案例A", "stage": "异议处理"},
            citations=[],
            source_file="case.sales_insights.json",
        ),
        RagChunk(
            chunk_id="chunk-4",
            chunk_type="training_course",
            text="课程内容",
            metadata={"course_name": "促成课程"},
            citations=[],
            source_file="course.pptx",
        ),
    ]

    store.ensure_collection(vector_dim=3)
    store.upsert(
        [
            MilvusChunkRecord.from_chunk(chunk, vector=[0.1, 0.2, 0.3])
            for chunk in chunks
        ]
    )

    options = store.filter_options()

    assert options == {
        "chunk_types": ["objection_handling", "script", "training_course"],
        "stages": ["售前", "异议处理"],
        "case_names": ["案例A", "案例B"],
        "source_kinds": [],
        "primary_domains": [],
    }


def test_multi_collection_store_merges_filter_options(tmp_path) -> None:
    case_store = _lite_store_with_chunks(
        tmp_path,
        "case_chunks",
        [
            (
                RagChunk(
                    chunk_id="case-1",
                    chunk_type="script",
                    text="售前话术",
                    metadata={"case_name": "案例A", "stage": "售前"},
                    citations=[],
                    source_file="case.sales_insights.json",
                ),
                [1.0, 0.0, 0.0],
            )
        ],
    )
    course_store = _lite_store_with_chunks(
        tmp_path,
        "course_chunks",
        [
            (
                RagChunk(
                    chunk_id="course-1",
                    chunk_type="training_course",
                    text="课程内容",
                    metadata={"course_name": "促成课程"},
                    citations=[],
                    source_file="course.pptx",
                ),
                [0.9, 0.1, 0.0],
            )
        ],
    )
    store = milvus_store.MultiCollectionStore([case_store, course_store])

    options = store.filter_options()

    assert options == {
        "chunk_types": ["script", "training_course"],
        "stages": ["售前"],
        "case_names": ["案例A"],
        "source_kinds": [],
        "primary_domains": [],
    }


def test_store_deletes_and_restores_complete_raw_rows(tmp_path) -> None:
    store = MilvusLiteStore(db_path=tmp_path / "rag.db", collection_name="chunks")
    original_chunk = RagChunk(
        chunk_id="same",
        chunk_type="script",
        text="旧文本",
        metadata={
            "case_name": "案例A",
            "stage": "售前",
            "scenario": "客户抗拒",
            "strategy_names": ["风险唤醒"],
        },
        citations=[EvidenceRef(section_name="第1节", quote="原文")],
        source_file="demo",
    )
    original = MilvusChunkRecord.from_chunk(original_chunk, [0.1, 0.2])
    store.ensure_collection(2)
    store.upsert([original])

    snapshot = store.fetch_raw_rows_by_ids(["same"])
    store.delete_by_ids(["same"])
    store.flush()

    assert store.fetch_raw_rows_by_ids(["same"]) == {}

    store.upsert_raw_rows(list(snapshot.values()))
    store.flush()

    restored = store.fetch_raw_rows_by_ids(["same"])["same"]
    expected = original.to_row()
    scalar_fields = [
        "chunk_id",
        "text",
        "text_hash",
        "case_name",
        "chunk_type",
        "stage",
        "scenario",
        "metadata_json",
        "citations_json",
    ]
    assert {field: restored[field] for field in scalar_fields} == {
        field: expected[field] for field in scalar_fields
    }
    assert restored["vector"] == pytest.approx([0.1, 0.2])
