import json
from types import SimpleNamespace

import xhbx_rag.cli as cli


def _fake_config() -> SimpleNamespace:
    return SimpleNamespace(
        base_url="https://api.example.com/v1",
        api_key="chat-key",
        model_name="chat-model",
        embedding_base_url="https://api.siliconflow.com/v1",
        embedding_api_key="embedding-key",
        embedding_model_name="embedding-model",
        rerank_base_url="https://api.siliconflow.com/v1",
        rerank_api_key="rerank-key",
        rerank_model_name="rerank-model",
        milvus_mode="lite",
        milvus_lite_path="local.db",
        milvus_uri="http://localhost:19530",
        milvus_token="",
        milvus_collection="chunks",
        milvus_course_collection="course_chunks",
    )


def test_cli_milvus_store_uses_shared_factory(monkeypatch) -> None:
    config = _fake_config()
    calls = {}

    def fake_create_milvus_store(received_config):
        calls["config"] = received_config
        return "store"

    monkeypatch.setattr(cli, "create_milvus_store", fake_create_milvus_store)

    assert cli._milvus_store(config) == "store"
    assert calls["config"] is config


def test_cli_index_uses_retrieval_components(monkeypatch, tmp_path) -> None:
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text("", encoding="utf-8")
    calls = {}

    monkeypatch.setattr(cli.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(cli, "EmbeddingClient", lambda **kwargs: "embedding")
    monkeypatch.setattr(cli, "create_milvus_store", lambda config: "store")

    def fake_index_chunks(chunks_path, embedding_client, store, trace=None, mode="incremental"):
        calls["chunks_path"] = chunks_path
        calls["embedding_client"] = embedding_client
        calls["store"] = store
        calls["trace"] = trace
        calls["mode"] = mode
        return 3

    monkeypatch.setattr(cli, "index_chunks", fake_index_chunks)

    exit_code = cli.main(["index", "--chunks", str(chunks)])

    assert exit_code == 0
    assert calls == {
        "chunks_path": chunks,
        "embedding_client": "embedding",
        "store": "store",
        "trace": None,
        "mode": "incremental",
    }


def test_cli_index_passes_rebuild_mode(monkeypatch, tmp_path) -> None:
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text("", encoding="utf-8")
    calls = {}

    monkeypatch.setattr(cli.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(cli, "EmbeddingClient", lambda **kwargs: "embedding")
    monkeypatch.setattr(cli, "create_milvus_store", lambda config: "store")

    def fake_index_chunks(chunks_path, embedding_client, store, trace=None, mode="incremental"):
        calls["chunks_path"] = chunks_path
        calls["embedding_client"] = embedding_client
        calls["store"] = store
        calls["trace"] = trace
        calls["mode"] = mode
        return 3

    monkeypatch.setattr(cli, "index_chunks", fake_index_chunks)

    exit_code = cli.main(["index", "--chunks", str(chunks), "--mode", "rebuild"])

    assert exit_code == 0
    assert calls == {
        "chunks_path": chunks,
        "embedding_client": "embedding",
        "store": "store",
        "trace": None,
        "mode": "rebuild",
    }


def test_cli_index_course_collection_targets_course_store(monkeypatch, tmp_path) -> None:
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text("", encoding="utf-8")
    calls = {}

    monkeypatch.setattr(cli.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(cli, "EmbeddingClient", lambda **kwargs: "embedding")

    def fake_create_milvus_store(config, collection_name=None):
        calls["collection_name"] = collection_name
        return "course-store"

    monkeypatch.setattr(cli, "create_milvus_store", fake_create_milvus_store)
    monkeypatch.setattr(
        cli, "index_chunks", lambda chunks_path, embedding_client, store, trace=None, mode="incremental": 1
    )

    exit_code = cli.main(["index", "--chunks", str(chunks), "--collection", "course"])

    assert exit_code == 0
    assert calls["collection_name"] == "course_chunks"


def test_cli_parse_course_passes_none_agent_when_no_enrich(monkeypatch, tmp_path, capsys) -> None:
    course_dir = tmp_path / "课程"
    course_dir.mkdir()
    out_dir = tmp_path / "out"
    calls = {}

    monkeypatch.setattr(cli.RetrievalConfig, "from_env", _fake_config)

    def fake_parse_course_dir(course_dir, out_dir, enrichment_agent=None, trace=None):
        calls["course_dir"] = course_dir
        calls["enrichment_agent"] = enrichment_agent
        return SimpleNamespace(
            counts={"files_parsed": 1, "chunks": 2},
            to_json=lambda: json.dumps({"counts": {"files_parsed": 1, "chunks": 2}}),
            output_files={"chunks": str(out_dir / "chunks.jsonl")},
        )

    monkeypatch.setattr(cli, "parse_course_dir", fake_parse_course_dir)

    exit_code = cli.main(
        ["parse-course", "--course-dir", str(course_dir), "--out", str(out_dir), "--no-enrich"]
    )

    assert exit_code == 0
    assert calls["course_dir"] == course_dir
    assert calls["enrichment_agent"] is None
    assert json.loads(capsys.readouterr().out)["counts"]["chunks"] == 2


def test_cli_parse_course_builds_enrichment_agent_by_default(monkeypatch, tmp_path) -> None:
    course_dir = tmp_path / "课程"
    course_dir.mkdir()
    calls = {}

    monkeypatch.setattr(cli.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(
        cli, "CourseEnrichmentAgentScopeAgent", lambda **kwargs: "enrich-agent"
    )

    def fake_parse_course_dir(course_dir, out_dir, enrichment_agent=None, trace=None):
        calls["enrichment_agent"] = enrichment_agent
        return SimpleNamespace(counts={}, to_json=lambda: "{}", output_files={})

    monkeypatch.setattr(cli, "parse_course_dir", fake_parse_course_dir)

    exit_code = cli.main(
        ["parse-course", "--course-dir", str(course_dir), "--out", str(tmp_path / "out")]
    )

    assert exit_code == 0
    assert calls["enrichment_agent"] == "enrich-agent"


def test_cli_ingest_course_runs_parse_then_index(monkeypatch, tmp_path, capsys) -> None:
    course_dir = tmp_path / "课程"
    course_dir.mkdir()
    out_dir = tmp_path / "out"
    calls = {}

    monkeypatch.setattr(cli.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(cli, "EmbeddingClient", lambda **kwargs: "embedding")

    def fake_create_milvus_store(config, collection_name=None):
        calls["collection_name"] = collection_name
        return "course-store"

    monkeypatch.setattr(cli, "create_milvus_store", fake_create_milvus_store)

    chunks_path = out_dir / "chunks.jsonl"

    def fake_parse_course_dir(course_dir, out_dir, enrichment_agent=None, trace=None):
        calls["parsed"] = True
        return SimpleNamespace(
            counts={"chunks": 2},
            to_json=lambda: "{}",
            output_files={"chunks": str(chunks_path)},
        )

    monkeypatch.setattr(cli, "parse_course_dir", fake_parse_course_dir)

    def fake_index_chunks(chunks_path_arg, embedding_client, store, trace=None, mode="incremental"):
        calls["indexed_path"] = chunks_path_arg
        calls["mode"] = mode
        calls["store"] = store
        return 2

    monkeypatch.setattr(cli, "index_chunks", fake_index_chunks)

    exit_code = cli.main(
        ["ingest-course", "--course-dir", str(course_dir), "--out", str(out_dir), "--no-enrich"]
    )

    assert exit_code == 0
    assert calls["parsed"] is True
    assert calls["indexed_path"] == chunks_path
    assert calls["collection_name"] == "course_chunks"
    assert calls["store"] == "course-store"
    summary = json.loads(capsys.readouterr().out)
    assert summary["index"]["indexed"] == 2


def test_cli_search_prints_json_result(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(cli, "QueryUnderstandingAgent", lambda **kwargs: "agent")
    monkeypatch.setattr(cli, "EmbeddingClient", lambda **kwargs: "embedding")
    monkeypatch.setattr(cli, "create_retrieval_store", lambda config: "store")
    monkeypatch.setattr(cli, "RerankClient", lambda **kwargs: "reranker")
    monkeypatch.setattr(
        cli,
        "search_evidence",
        lambda **kwargs: {
            "original_query": "客户不想聊保险怎么开场？",
            "rewritten_query": "客户抗拒谈保险时如何开场",
            "intent": "script_search",
            "filters": {"chunk_types": ["script"]},
            "results": [{"chunk_id": "c1"}],
        },
    )

    exit_code = cli.main(
        [
            "search",
            "--query",
            "客户不想聊保险怎么开场？",
            "--top-n",
            "20",
            "--top-k",
            "5",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["rewritten_query"] == "客户抗拒谈保险时如何开场"
    assert output["results"][0]["chunk_id"] == "c1"


def test_cli_answer_prints_json_result(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(cli, "QueryUnderstandingAgent", lambda **kwargs: "agent")
    monkeypatch.setattr(cli, "EmbeddingClient", lambda **kwargs: "embedding")
    monkeypatch.setattr(cli, "create_retrieval_store", lambda config: "store")
    monkeypatch.setattr(cli, "RerankClient", lambda **kwargs: "reranker")
    monkeypatch.setattr(cli, "AnswerAgent", lambda **kwargs: "answer-agent")

    def fake_answer_query(**kwargs):
        assert kwargs["query"] == "保单整理对客户有什么作用？"
        assert kwargs["query_agent"] == "agent"
        assert kwargs["embedding_client"] == "embedding"
        assert kwargs["store"] == "store"
        assert kwargs["reranker"] == "reranker"
        assert kwargs["answer_agent"] == "answer-agent"
        assert kwargs["top_n"] == 20
        assert kwargs["top_k"] == 5
        assert kwargs["trace"] is None
        return {
            "original_query": "保单整理对客户有什么作用？",
            "rewritten_query": "保单整理对客户的作用和价值是什么？",
            "intent": "general_sales_qa",
            "answer": "保单整理能帮助客户看清保障缺口。",
            "citations": [{"filename": "第3节.docx"}],
            "evidence_count": 5,
        }

    monkeypatch.setattr(cli, "answer_query", fake_answer_query)

    exit_code = cli.main(
        [
            "answer",
            "--query",
            "保单整理对客户有什么作用？",
            "--top-n",
            "20",
            "--top-k",
            "5",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "保单整理能帮助客户看清保障缺口。"
    assert output["citations"][0]["filename"] == "第3节.docx"


def test_cli_search_trace_writes_step_events_to_stderr(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(cli, "QueryUnderstandingAgent", lambda **kwargs: "agent")
    monkeypatch.setattr(cli, "EmbeddingClient", lambda **kwargs: "embedding")
    monkeypatch.setattr(cli, "create_retrieval_store", lambda config: "store")
    monkeypatch.setattr(cli, "RerankClient", lambda **kwargs: "reranker")

    def fake_search_evidence(**kwargs):
        kwargs["trace"].emit("search.query_understood", {"rewritten_query": "客户抗拒谈保险"})
        return {
            "original_query": "客户不想聊保险怎么开场？",
            "rewritten_query": "客户抗拒谈保险",
            "intent": "script_search",
            "filters": {"chunk_types": ["script"]},
            "results": [{"chunk_id": "c1"}],
        }

    monkeypatch.setattr(cli, "search_evidence", fake_search_evidence)

    exit_code = cli.main(
        [
            "search",
            "--query",
            "客户不想聊保险怎么开场？",
            "--trace",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out)["results"][0]["chunk_id"] == "c1"
    trace_event = json.loads(captured.err)
    assert trace_event["step"] == "search.query_understood"
    assert trace_event["payload"]["rewritten_query"] == "客户抗拒谈保险"


def test_cli_search_studio_uses_studio_trace_sink(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(cli, "QueryUnderstandingAgent", lambda **kwargs: "agent")
    monkeypatch.setattr(cli, "EmbeddingClient", lambda **kwargs: "embedding")
    monkeypatch.setattr(cli, "create_retrieval_store", lambda config: "store")
    monkeypatch.setattr(cli, "RerankClient", lambda **kwargs: "reranker")
    calls = {}

    class _FakeStudioSink:
        def emit(self, step, payload):
            calls["emitted"] = {"step": step, "payload": payload}

        def close(self):
            calls["closed"] = True

    def fake_create_studio_trace_sink(*, endpoint, root_name):
        calls["studio_endpoint"] = endpoint
        calls["root_name"] = root_name
        return _FakeStudioSink()

    def fake_search_evidence(**kwargs):
        calls["trace"] = kwargs["trace"]
        kwargs["trace"].emit("search.completed", {"result_count": 1})
        return {
            "original_query": "客户不想聊保险怎么开场？",
            "rewritten_query": "客户抗拒谈保险",
            "intent": "script_search",
            "filters": {"chunk_types": ["script"]},
            "results": [{"chunk_id": "c1"}],
        }

    monkeypatch.setattr(cli, "create_studio_trace_sink", fake_create_studio_trace_sink)
    monkeypatch.setattr(cli, "search_evidence", fake_search_evidence)

    exit_code = cli.main(
        [
            "search",
            "--query",
            "客户不想聊保险怎么开场？",
            "--studio",
            "--studio-endpoint",
            "localhost:4317",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["chunk_id"] == "c1"
    assert calls["studio_endpoint"] == "localhost:4317"
    assert calls["root_name"] == "xhbx-rag.search"
    assert calls["emitted"] == {"step": "search.completed", "payload": {"result_count": 1}}
    assert calls["closed"] is True
