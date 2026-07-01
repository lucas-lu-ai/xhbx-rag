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


def _install_rag_stubs(monkeypatch, citations=None):
    constructor_calls = {}
    answer_calls = {}

    def component_factory(name: str, value: str):
        def factory(**kwargs):
            constructor_calls[name] = kwargs
            return value

        return factory

    monkeypatch.setattr(services.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(
        services,
        "QueryUnderstandingAgent",
        component_factory("query_agent", "query-agent"),
    )
    monkeypatch.setattr(
        services,
        "EmbeddingClient",
        component_factory("embedding_client", "embedding"),
    )
    monkeypatch.setattr(
        services,
        "MilvusLiteStore",
        component_factory("store", "store"),
    )
    monkeypatch.setattr(
        services,
        "RerankClient",
        component_factory("reranker", "reranker"),
    )
    monkeypatch.setattr(
        services,
        "AnswerAgent",
        component_factory("answer_agent", "answer-agent"),
    )

    def fake_answer_query(**kwargs):
        answer_calls.update(kwargs)
        return {
            "original_query": kwargs["query"],
            "rewritten_query": "客户预算上限80万时如何回应",
            "intent": "objection_handling",
            "filters": {},
            "answer": "先承接预算，再讨论缴费期和保障缺口。",
            "citations": citations if citations is not None else [],
            "evidence_count": 1,
        }

    monkeypatch.setattr(services, "answer_query", fake_answer_query)
    return constructor_calls, answer_calls


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


def test_get_status_reports_value_error(monkeypatch, tmp_path: Path) -> None:
    def fail_from_env():
        raise ValueError("MILVUS_VECTOR_DIM 必须是整数")

    monkeypatch.setattr(services.RetrievalConfig, "from_env", fail_from_env)

    status = services.get_status(project_root=tmp_path)

    assert status["ok"] is False
    assert status["errors"] == ["MILVUS_VECTOR_DIM 必须是整数"]


def test_get_status_marks_multiple_missing_config_keys(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail_from_env():
        raise ConfigError("缺少必要环境变量: API_KEY, RERANK_API_KEY")

    monkeypatch.setattr(services.RetrievalConfig, "from_env", fail_from_env)

    status = services.get_status(project_root=tmp_path)

    assert status["ok"] is False
    assert status["config"]["API_KEY"] is False
    assert status["config"]["RERANK_API_KEY"] is False
    assert status["config"]["EMBEDDING_API_KEY"] is True
    assert status["errors"] == ["缺少必要环境变量: API_KEY, RERANK_API_KEY"]


def test_answer_question_uses_existing_rag_components(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "data" / "案例A" / "第1节.track-0.txt"
    source.parent.mkdir(parents=True)
    source.write_text("客户说每年保费预算不能超过80万", encoding="utf-8")
    constructors, calls = _install_rag_stubs(
        monkeypatch,
        citations=[
            {
                "filename": "第1节.track-0.txt",
                "source_type": "txt",
                "source_path": "data/案例A/第1节.track-0.txt",
                "locator": {"line_start": 2, "line_end": 2},
                "locator_confidence": "validated_span",
                "source_excerpt": "客户说每年保费预算不能超过80万",
            }
        ],
    )

    result = services.answer_question(
        query="客户说每年不能超过80万怎么办？",
        top_n=20,
        top_k=5,
        project_root=tmp_path,
    )

    assert constructors["query_agent"] == {
        "base_url": "https://api.example.com/v1",
        "api_key": "chat-key",
        "model": "chat-model",
    }
    assert constructors["embedding_client"] == {
        "base_url": "https://api.siliconflow.com/v1",
        "api_key": "embedding-key",
        "model": "embedding-model",
    }
    assert constructors["store"] == {
        "db_path": Path(".local/milvus/xhbx_rag.db"),
        "collection_name": "xhbx_sales_chunks",
    }
    assert constructors["reranker"] == {
        "base_url": "https://api.siliconflow.com/v1",
        "api_key": "rerank-key",
        "model": "rerank-model",
    }
    assert constructors["answer_agent"] == {
        "base_url": "https://api.example.com/v1",
        "api_key": "chat-key",
        "model": "chat-model",
    }
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


def test_answer_question_strips_query_before_rag_call(monkeypatch) -> None:
    _, calls = _install_rag_stubs(monkeypatch)

    services.answer_question(query=" q ", top_n=20, top_k=5)

    assert calls["query"] == "q"


@pytest.mark.parametrize(
    ("top_n", "top_k", "message"),
    [
        (0, 5, "top_n 必须在 1 到 100 之间"),
        (20, 0, "top_k 必须在 1 到 20 之间"),
        (5, 6, "top_k 不能大于 top_n"),
        (101, 5, "top_n 必须在 1 到 100 之间"),
        (20, 21, "top_k 必须在 1 到 20 之间"),
    ],
)
def test_answer_question_rejects_invalid_limits(
    monkeypatch,
    top_n: int,
    top_k: int,
    message: str,
) -> None:
    def fail_from_env():
        raise AssertionError("非法 top_n/top_k 不应加载配置")

    monkeypatch.setattr(services.RetrievalConfig, "from_env", fail_from_env)

    with pytest.raises(ValueError, match=message):
        services.answer_question(query="q", top_n=top_n, top_k=top_k)


def test_answer_question_marks_unrevealed_source_and_uses_quote_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _install_rag_stubs(
        monkeypatch,
        citations=[
            {
                "filename": "missing.txt",
                "source_type": "txt",
                "source_path": "data/案例A/missing.txt",
                "locator": {},
                "quote": "备用摘录",
            }
        ],
    )

    result = services.answer_question(
        query="q",
        top_n=20,
        top_k=5,
        project_root=tmp_path,
    )

    assert result["citations"][0]["display_location"] == "未提供精确位置"
    assert result["citations"][0]["display_excerpt"] == "备用摘录"
    assert result["citations"][0]["can_reveal"] is False


def test_answer_question_handles_non_mapping_citation(monkeypatch) -> None:
    _install_rag_stubs(monkeypatch, citations=["plain citation"])

    result = services.answer_question(query="q", top_n=20, top_k=5)

    assert result["citations"] == [
        {
            "display_location": "未提供精确位置",
            "display_excerpt": "plain citation",
            "can_reveal": False,
        }
    ]


def test_answer_question_rejects_empty_query() -> None:
    with pytest.raises(ValueError, match="问题不能为空"):
        services.answer_question(query="  ", top_n=20, top_k=5)
