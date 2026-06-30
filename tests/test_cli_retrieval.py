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
        milvus_lite_path="local.db",
        milvus_collection="chunks",
    )


def test_cli_index_uses_retrieval_components(monkeypatch, tmp_path) -> None:
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text("", encoding="utf-8")
    calls = {}

    monkeypatch.setattr(cli.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(cli, "EmbeddingClient", lambda **kwargs: "embedding")
    monkeypatch.setattr(cli, "MilvusLiteStore", lambda **kwargs: "store")

    def fake_index_chunks(chunks_path, embedding_client, store, trace=None):
        calls["chunks_path"] = chunks_path
        calls["embedding_client"] = embedding_client
        calls["store"] = store
        calls["trace"] = trace
        return 3

    monkeypatch.setattr(cli, "index_chunks", fake_index_chunks)

    exit_code = cli.main(["index", "--chunks", str(chunks)])

    assert exit_code == 0
    assert calls == {
        "chunks_path": chunks,
        "embedding_client": "embedding",
        "store": "store",
        "trace": None,
    }


def test_cli_search_prints_json_result(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(cli, "QueryUnderstandingAgent", lambda **kwargs: "agent")
    monkeypatch.setattr(cli, "EmbeddingClient", lambda **kwargs: "embedding")
    monkeypatch.setattr(cli, "MilvusLiteStore", lambda **kwargs: "store")
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


def test_cli_search_trace_writes_step_events_to_stderr(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(cli, "QueryUnderstandingAgent", lambda **kwargs: "agent")
    monkeypatch.setattr(cli, "EmbeddingClient", lambda **kwargs: "embedding")
    monkeypatch.setattr(cli, "MilvusLiteStore", lambda **kwargs: "store")
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
