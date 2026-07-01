from pathlib import Path
from types import SimpleNamespace

import pytest

import xhbx_rag.web.services as services
from xhbx_rag.config import ConfigError


def _fake_config() -> SimpleNamespace:
    return SimpleNamespace(
        api_key="chat-key",
        base_url="https://api.example.com/v1",
        model_name="chat-model",
        embedding_base_url="https://api.siliconflow.com/v1",
        embedding_api_key="embedding-key",
        embedding_model_name="embedding-model",
        rerank_base_url="https://api.siliconflow.com/v1",
        rerank_api_key="rerank-key",
        rerank_model_name="rerank-model",
        milvus_lite_path=Path(".local/milvus/xhbx_rag.db"),
        milvus_collection="xhbx_sales_chunks",
    )


def test_get_status_reports_config_success(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(services.RetrievalConfig, "from_env", _fake_config)

    status = services.get_status(project_root=tmp_path)

    assert status["ok"] is True
    assert status["data_dir"] == str(tmp_path / "data")
    assert status["milvus_collection"] == "xhbx_sales_chunks"
    assert status["config"]["API_KEY"] is True
    assert status["config"]["EMBEDDING_API_KEY"] is True
    assert status["errors"] == []


def test_get_status_reports_config_error(monkeypatch, tmp_path: Path) -> None:
    def fail_from_env():
        raise ConfigError("缺少必要环境变量: API_KEY")

    monkeypatch.setattr(services.RetrievalConfig, "from_env", fail_from_env)

    status = services.get_status(project_root=tmp_path)

    assert status["ok"] is False
    assert status["config"]["API_KEY"] is False
    assert status["errors"] == ["缺少必要环境变量: API_KEY"]


def test_answer_question_uses_existing_rag_components(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "data" / "案例A" / "第1节.track-0.txt"
    source.parent.mkdir(parents=True)
    source.write_text("客户说每年保费预算不能超过80万", encoding="utf-8")
    calls = {}

    monkeypatch.setattr(services.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(services, "QueryUnderstandingAgent", lambda **kwargs: "query-agent")
    monkeypatch.setattr(services, "EmbeddingClient", lambda **kwargs: "embedding")
    monkeypatch.setattr(services, "MilvusLiteStore", lambda **kwargs: "store")
    monkeypatch.setattr(services, "RerankClient", lambda **kwargs: "reranker")
    monkeypatch.setattr(services, "AnswerAgent", lambda **kwargs: "answer-agent")

    def fake_answer_query(**kwargs):
        calls.update(kwargs)
        return {
            "original_query": "客户说每年不能超过80万怎么办？",
            "rewritten_query": "客户预算上限80万时如何回应",
            "intent": "objection_handling",
            "filters": {},
            "answer": "先承接预算，再讨论缴费期和保障缺口。",
            "citations": [
                {
                    "filename": "第1节.track-0.txt",
                    "source_type": "txt",
                    "source_path": "data/案例A/第1节.track-0.txt",
                    "locator": {"line_start": 2, "line_end": 2},
                    "locator_confidence": "validated_span",
                    "source_excerpt": "客户说每年保费预算不能超过80万",
                }
            ],
            "evidence_count": 1,
        }

    monkeypatch.setattr(services, "answer_query", fake_answer_query)

    result = services.answer_question(
        query="客户说每年不能超过80万怎么办？",
        top_n=20,
        top_k=5,
        project_root=tmp_path,
    )

    assert calls["query"] == "客户说每年不能超过80万怎么办？"
    assert calls["query_agent"] == "query-agent"
    assert calls["embedding_client"] == "embedding"
    assert calls["store"] == "store"
    assert calls["reranker"] == "reranker"
    assert calls["answer_agent"] == "answer-agent"
    assert calls["top_n"] == 20
    assert calls["top_k"] == 5
    assert result["answer"] == "先承接预算，再讨论缴费期和保障缺口。"
    assert result["citations"][0]["display_location"] == "L2"
    assert result["citations"][0]["display_excerpt"] == "客户说每年保费预算不能超过80万"
    assert result["citations"][0]["can_reveal"] is True


def test_answer_question_rejects_empty_query() -> None:
    with pytest.raises(ValueError, match="问题不能为空"):
        services.answer_question(query="  ", top_n=20, top_k=5)
