import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import xhbx_rag.web.services as services
from xhbx_rag.config import ConfigError


SAFE_CONFIG_ERROR = "配置解析失败，请检查 .env 中的数值配置。"
LOCAL_INDEX_ERROR = "本地 Milvus 索引暂时不可用，请关闭其他正在使用索引的进程后重试。"


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
        milvus_mode="lite",
        milvus_lite_path=Path(".local/milvus/xhbx_rag.db"),
        milvus_uri="http://localhost:19530",
        milvus_token="",
        milvus_collection="xhbx_sales_chunks",
    )


class _FakeCloseable:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    def close(self) -> None:
        self.calls.append(self.name)


class _FakeHttpComponent:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.http_client = _FakeCloseable(f"{name}.http_client", calls)


class _FakeStoreComponent:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.client = _FakeCloseable(f"{name}.client", calls)


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
    def store_factory(config):
        constructor_calls["store"] = config
        return "store"

    monkeypatch.setattr(services, "create_milvus_store", store_factory)
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


def _install_closeable_rag_stubs(monkeypatch, error=None) -> list[str]:
    close_calls = []

    def http_factory(name: str):
        def factory(**kwargs):
            return _FakeHttpComponent(name, close_calls)

        return factory

    def store_factory(config):
        return _FakeStoreComponent("store", close_calls)

    monkeypatch.setattr(services.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(services, "QueryUnderstandingAgent", http_factory("query_agent"))
    monkeypatch.setattr(services, "EmbeddingClient", http_factory("embedding_client"))
    monkeypatch.setattr(services, "create_milvus_store", store_factory)
    monkeypatch.setattr(services, "RerankClient", http_factory("reranker"))
    monkeypatch.setattr(services, "AnswerAgent", http_factory("answer_agent"))

    def fake_answer_query(**kwargs):
        if error is not None:
            raise error
        return {
            "original_query": kwargs["query"],
            "rewritten_query": "q",
            "intent": "general_sales_qa",
            "filters": {},
            "answer": "answer",
            "citations": [],
            "evidence_count": 0,
        }

    monkeypatch.setattr(services, "answer_query", fake_answer_query)
    return close_calls


def test_get_status_reports_config_success(monkeypatch) -> None:
    monkeypatch.setattr(services.RetrievalConfig, "from_env", _fake_config)

    status = services.get_status()

    assert status["ok"] is True
    assert status["data_dir"] == "data"
    assert status["milvus_mode"] == "lite"
    assert status["milvus_target"] == ".local/milvus/xhbx_rag.db"
    assert status["milvus_collection"] == "xhbx_sales_chunks"
    assert status["config"]["API_KEY"] is True
    assert status["config"]["EMBEDDING_API_KEY"] is True
    assert status["errors"] == []


def test_get_status_reports_docker_milvus_target(monkeypatch) -> None:
    def docker_config():
        config = _fake_config()
        config.milvus_mode = "docker"
        config.milvus_uri = "http://127.0.0.1:19530"
        config.milvus_token = "root:Milvus"
        return config

    monkeypatch.setattr(services.RetrievalConfig, "from_env", docker_config)

    status = services.get_status()

    assert status["milvus_mode"] == "docker"
    assert status["milvus_target"] == "http://127.0.0.1:19530"
    assert "root:Milvus" not in str(status)


def test_get_status_reports_serial_batch_concurrency_for_lite(monkeypatch) -> None:
    monkeypatch.setattr(services.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(services, "load_env_values", lambda: {})

    status = services.get_status()

    assert status["batch_concurrency"] == 1


def test_get_status_reports_default_batch_concurrency_for_docker(monkeypatch) -> None:
    def docker_config():
        config = _fake_config()
        config.milvus_mode = "docker"
        return config

    monkeypatch.setattr(services.RetrievalConfig, "from_env", docker_config)
    monkeypatch.setattr(services, "load_env_values", lambda: {})

    status = services.get_status()

    assert status["batch_concurrency"] == 3


def test_get_status_reads_batch_concurrency_from_env_for_docker(monkeypatch) -> None:
    def docker_config():
        config = _fake_config()
        config.milvus_mode = "docker"
        return config

    monkeypatch.setattr(services.RetrievalConfig, "from_env", docker_config)
    monkeypatch.setattr(
        services, "load_env_values", lambda: {"WEB_BATCH_CONCURRENCY": "5"}
    )

    status = services.get_status()

    assert status["batch_concurrency"] == 5


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("abc", 3),
        ("", 3),
        ("0", 1),
        ("-2", 1),
        ("999", 10),
        ("2.5", 3),
    ],
)
def test_get_status_sanitizes_invalid_batch_concurrency(
    monkeypatch, raw_value: str, expected: int
) -> None:
    def docker_config():
        config = _fake_config()
        config.milvus_mode = "docker"
        return config

    monkeypatch.setattr(services.RetrievalConfig, "from_env", docker_config)
    monkeypatch.setattr(
        services, "load_env_values", lambda: {"WEB_BATCH_CONCURRENCY": raw_value}
    )

    status = services.get_status()

    assert status["batch_concurrency"] == expected


def test_get_status_ignores_batch_concurrency_env_for_lite(monkeypatch) -> None:
    monkeypatch.setattr(services.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(
        services, "load_env_values", lambda: {"WEB_BATCH_CONCURRENCY": "5"}
    )

    status = services.get_status()

    assert status["batch_concurrency"] == 1


def test_get_status_reports_serial_batch_concurrency_on_config_error(
    monkeypatch,
) -> None:
    def fail_from_env():
        raise ConfigError("缺少必要环境变量: API_KEY")

    monkeypatch.setattr(services.RetrievalConfig, "from_env", fail_from_env)

    status = services.get_status()

    assert status["batch_concurrency"] == 1


def test_get_status_reports_config_error(monkeypatch) -> None:
    def fail_from_env():
        raise ConfigError("缺少必要环境变量: API_KEY")

    monkeypatch.setattr(services.RetrievalConfig, "from_env", fail_from_env)

    status = services.get_status()

    assert status["ok"] is False
    assert status["config"]["API_KEY"] is False
    assert status["errors"] == ["缺少必要环境变量: API_KEY"]


def test_get_status_sanitizes_non_config_value_error(monkeypatch) -> None:
    def fail_from_env():
        raise ValueError("invalid literal for int() with base 10: 'sk-secret-value'")

    monkeypatch.setattr(services.RetrievalConfig, "from_env", fail_from_env)

    status = services.get_status()

    assert status["ok"] is False
    assert status["errors"] == [SAFE_CONFIG_ERROR]
    assert "sk-secret-value" not in "\n".join(status["errors"])
    assert status["config"]["API_KEY"] is True


def test_get_status_marks_multiple_missing_config_keys(monkeypatch) -> None:
    def fail_from_env():
        raise ConfigError("缺少必要环境变量: API_KEY, RERANK_API_KEY")

    monkeypatch.setattr(services.RetrievalConfig, "from_env", fail_from_env)

    status = services.get_status()

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
    assert constructors["store"].milvus_mode == "lite"
    assert constructors["store"].milvus_lite_path == Path(".local/milvus/xhbx_rag.db")
    assert constructors["store"].milvus_collection == "xhbx_sales_chunks"
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
    assert calls["answer_agent"].agent == "answer-agent"
    assert calls["top_n"] == 20
    assert calls["top_k"] == 5
    assert result["answer"] == "先承接预算，再讨论缴费期和保障缺口。"
    assert result["citations"][0]["display_location"] == "L2"
    assert result["citations"][0]["display_excerpt"] == "客户说每年保费预算不能超过80万"
    assert result["citations"][0]["can_reveal"] is True


def test_answer_question_returns_retrieval_evidences_from_answer_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "data" / "案例A" / "第2节.track-0.txt"
    source.parent.mkdir(parents=True)
    source.write_text("客户担心预算，可以先承接预算，再对齐保障缺口。", encoding="utf-8")

    monkeypatch.setattr(services.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(services, "QueryUnderstandingAgent", lambda **kwargs: "query")
    monkeypatch.setattr(services, "EmbeddingClient", lambda **kwargs: "embedding")
    monkeypatch.setattr(services, "create_milvus_store", lambda config: "store")
    monkeypatch.setattr(services, "RerankClient", lambda **kwargs: "reranker")

    class FakeAnswerAgent:
        def generate(self, search_result: dict):
            return object()

    monkeypatch.setattr(services, "AnswerAgent", lambda **kwargs: FakeAnswerAgent())

    def fake_answer_query(**kwargs):
        search_result = {
            "results": [
                {
                    "chunk_id": "case-a-2",
                    "chunk_type": "objection_handling",
                    "text": "客户担心预算，可以先承接预算，再对齐保障缺口。",
                    "score": 0.82,
                    "rerank_score": 0.91,
                    "metadata": {"case_name": "案例A", "stage": "需求分析"},
                    "citations": [
                        {
                            "filename": "第2节.track-0.txt",
                            "source_type": "txt",
                            "source_path": "data/案例A/第2节.track-0.txt",
                            "locator": {"line_start": 1, "line_end": 1},
                            "locator_confidence": "validated_span",
                            "source_excerpt": (
                                "客户担心预算，可以先承接预算，再对齐保障缺口。"
                            ),
                        }
                    ],
                }
            ]
        }
        kwargs["answer_agent"].generate(search_result)
        return {
            "original_query": kwargs["query"],
            "rewritten_query": "客户预算异议处理",
            "intent": "objection_handling",
            "filters": {},
            "answer": "先承接预算，再对齐保障缺口。",
            "citations": [],
            "evidence_count": 1,
        }

    monkeypatch.setattr(services, "answer_query", fake_answer_query)

    result = services.answer_question(
        query="客户说预算有限怎么办？",
        top_n=20,
        top_k=5,
        project_root=tmp_path,
    )

    assert result["retrieval_evidences"] == [
        {
            "chunk_id": "case-a-2",
            "chunk_type": "objection_handling",
            "text": "客户担心预算，可以先承接预算，再对齐保障缺口。",
            "score": 0.82,
            "rerank_score": 0.91,
            "metadata": {"case_name": "案例A", "stage": "需求分析"},
            "citations": [
                {
                    "filename": "第2节.track-0.txt",
                    "source_type": "txt",
                    "source_path": "data/案例A/第2节.track-0.txt",
                    "locator": {"line_start": 1, "line_end": 1},
                    "locator_confidence": "validated_span",
                    "source_excerpt": "客户担心预算，可以先承接预算，再对齐保障缺口。",
                    "display_location": "L1",
                    "display_excerpt": "客户担心预算，可以先承接预算，再对齐保障缺口。",
                    "can_reveal": True,
                }
            ],
        }
    ]


def test_answer_question_passes_trace_to_rag_chain(monkeypatch, tmp_path: Path) -> None:
    constructors, calls = _install_rag_stubs(monkeypatch)
    trace = object()

    result = services.answer_question(
        query="客户说每年不能超过80万怎么办？",
        top_n=20,
        top_k=5,
        project_root=tmp_path,
        trace=trace,
    )

    assert result["answer"] == "先承接预算，再讨论缴费期和保障缺口。"
    assert constructors["store"].milvus_mode == "lite"
    assert calls["trace"] is trace


def test_answer_question_stream_events_emit_steps_deltas_and_final(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fake_answer_question(*, query, top_n, top_k, project_root=None, trace=None):
        trace.emit("search.query_understood", {"rewritten_query": "预算不超过80万"})
        trace.emit(
            "search.tag_boosted",
            {
                "query_tag_paths": ["客户需求/保费预算"],
                "boosted_count": 1,
                "boosted": [
                    {
                        "chunk_id": "c1",
                        "matched_tag_paths": ["客户需求/保费预算"],
                        "boost_factor": 1.1,
                    }
                ],
            },
        )
        trace.emit("search.reranked", {"result_count": 2})
        return {
            "original_query": query,
            "rewritten_query": "预算不超过80万",
            "intent": "objection_handling",
            "filters": {},
            "answer": "先承接预算，再讨论缴费期和保障缺口。",
            "citations": [],
            "evidence_count": 2,
            "retrieval_evidences": [],
        }

    monkeypatch.setattr(services, "answer_question", fake_answer_question)

    events = list(
        services.answer_question_stream_events(
            query="客户说每年不能超过80万怎么办？",
            top_n=20,
            top_k=5,
            project_root=tmp_path,
        )
    )

    assert events[0]["type"] == "step"
    assert events[0]["step"] == "search.query_understood"
    assert events[0]["message"] == "已完成问题理解"
    assert events[0]["payload"] == {"rewritten_query": "预算不超过80万"}
    assert events[1]["step"] == "search.tag_boosted"
    assert events[1]["message"] == "已完成标签加权"
    assert events[1]["payload"]["query_tag_paths"] == ["客户需求/保费预算"]
    assert events[1]["payload"]["boosted"][0]["chunk_id"] == "c1"
    assert events[2]["step"] == "search.reranked"
    assert "".join(
        event["text"] for event in events if event["type"] == "answer_delta"
    ) == "先承接预算，再讨论缴费期和保障缺口。"
    assert events[-1]["type"] == "final"
    assert events[-1]["response"]["evidence_count"] == 2


def test_answer_question_stream_events_sends_trace_to_studio_when_enabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class FakeStudioTrace:
        def __init__(self) -> None:
            self.events = []
            self.closed = False

        def emit(self, step, payload):
            self.events.append((step, dict(payload)))

        def close(self):
            self.closed = True

    created = {}

    def fake_create_studio_trace_sink(*, endpoint, root_name):
        trace = FakeStudioTrace()
        created["endpoint"] = endpoint
        created["root_name"] = root_name
        created["trace"] = trace
        return trace

    def fake_answer_question(*, query, top_n, top_k, project_root=None, trace=None):
        trace.emit("search.query_understood", {"rewritten_query": "预算不超过80万"})
        trace.emit("answer.generated", {"citation_count": 0})
        return {
            "original_query": query,
            "rewritten_query": "预算不超过80万",
            "intent": "objection_handling",
            "filters": {},
            "answer": "先承接预算。",
            "citations": [],
            "evidence_count": 0,
            "retrieval_evidences": [],
        }

    monkeypatch.setenv("WEB_STUDIO_TRACE", "true")
    monkeypatch.setenv("WEB_STUDIO_ENDPOINT", "studio.example:4317")
    monkeypatch.setattr(
        services,
        "create_studio_trace_sink",
        fake_create_studio_trace_sink,
        raising=False,
    )
    monkeypatch.setattr(services, "answer_question", fake_answer_question)

    events = list(
        services.answer_question_stream_events(
            query="客户说每年不能超过80万怎么办？",
            top_n=20,
            top_k=5,
            project_root=tmp_path,
        )
    )

    assert created["endpoint"] == "studio.example:4317"
    assert created["root_name"] == "xhbx-rag.web.answer"
    assert created["trace"].events == [
        ("search.query_understood", {"rewritten_query": "预算不超过80万"}),
        ("answer.generated", {"citation_count": 0}),
    ]
    assert created["trace"].closed is True
    assert [event["step"] for event in events if event["type"] == "step"] == [
        "search.query_understood",
        "answer.generated",
    ]


def test_answer_question_stream_events_closes_studio_trace_on_exception(
    monkeypatch,
) -> None:
    class FakeStudioTrace:
        def __init__(self) -> None:
            self.closed = False

        def emit(self, step, payload):
            pass

        def close(self):
            self.closed = True

    created = {}

    def fake_create_studio_trace_sink(*, endpoint, root_name):
        trace = FakeStudioTrace()
        created["trace"] = trace
        return trace

    def fail_answer_question(*, query, top_n, top_k, project_root=None, trace=None):
        raise RuntimeError("secret-token leaked")

    monkeypatch.setenv("WEB_STUDIO_TRACE", "1")
    monkeypatch.setattr(
        services,
        "create_studio_trace_sink",
        fake_create_studio_trace_sink,
        raising=False,
    )
    monkeypatch.setattr(services, "answer_question", fail_answer_question)

    events = list(
        services.answer_question_stream_events(
            query="客户说每年不能超过80万怎么办？",
            top_n=20,
            top_k=5,
        )
    )

    assert events[0]["type"] == "_exception"
    assert created["trace"].closed is True


def test_answer_question_stream_events_emit_internal_exception(monkeypatch) -> None:
    def fail_answer_question(*, query, top_n, top_k, project_root=None, trace=None):
        raise RuntimeError("secret-token leaked")

    monkeypatch.setattr(services, "answer_question", fail_answer_question)

    events = list(
        services.answer_question_stream_events(
            query="客户说每年不能超过80万怎么办？",
            top_n=20,
            top_k=5,
        )
    )

    assert events[0]["type"] == "_exception"
    assert isinstance(events[0]["exception"], RuntimeError)


def test_answer_question_strips_query_before_rag_call(monkeypatch) -> None:
    _, calls = _install_rag_stubs(monkeypatch)

    services.answer_question(query=" q ", top_n=20, top_k=5)

    assert calls["query"] == "q"


def test_answer_question_preserves_config_error(monkeypatch) -> None:
    def fail_from_env():
        raise ConfigError("缺少必要环境变量: API_KEY")

    monkeypatch.setattr(services.RetrievalConfig, "from_env", fail_from_env)

    with pytest.raises(ValueError, match="缺少必要环境变量: API_KEY"):
        services.answer_question(query="q", top_n=20, top_k=5)


def test_answer_question_sanitizes_config_value_error(monkeypatch) -> None:
    def fail_from_env():
        raise ValueError("invalid literal for int() with base 10: 'sk-secret-value'")

    monkeypatch.setattr(services.RetrievalConfig, "from_env", fail_from_env)

    with pytest.raises(ValueError) as excinfo:
        services.answer_question(query="q", top_n=20, top_k=5)

    assert str(excinfo.value) == SAFE_CONFIG_ERROR
    assert "sk-secret-value" not in str(excinfo.value)


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


def test_answer_question_closes_resources_on_success(monkeypatch) -> None:
    close_calls = _install_closeable_rag_stubs(monkeypatch)

    result = services.answer_question(query="q", top_n=20, top_k=5)

    assert result["answer"] == "answer"
    assert close_calls == [
        "query_agent.http_client",
        "embedding_client.http_client",
        "store.client",
        "reranker.http_client",
        "answer_agent.http_client",
    ]


def test_answer_question_closes_resources_when_answer_query_fails(monkeypatch) -> None:
    error = RuntimeError("answer failed")
    close_calls = _install_closeable_rag_stubs(monkeypatch, error=error)

    with pytest.raises(RuntimeError, match="answer failed") as excinfo:
        services.answer_question(query="q", top_n=20, top_k=5)

    assert excinfo.value is error
    assert close_calls == [
        "query_agent.http_client",
        "embedding_client.http_client",
        "store.client",
        "reranker.http_client",
        "answer_agent.http_client",
    ]


def test_answer_question_rejects_empty_query() -> None:
    with pytest.raises(ValueError, match="问题不能为空"):
        services.answer_question(query="  ", top_n=20, top_k=5)


def test_answer_question_sanitizes_local_index_open_failure(monkeypatch) -> None:
    monkeypatch.setattr(services.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(
        services,
        "QueryUnderstandingAgent",
        lambda **kwargs: _FakeHttpComponent("query_agent", []),
    )
    monkeypatch.setattr(
        services,
        "EmbeddingClient",
        lambda **kwargs: _FakeHttpComponent("embedding_client", []),
    )

    def fail_store(config):
        raise RuntimeError(
            "Open local milvus failed for "
            "/Users/milan/xhbx-rag/.local/milvus/xhbx_rag.db secret-token"
        )

    monkeypatch.setattr(services, "create_milvus_store", fail_store)

    with pytest.raises(ValueError) as exc_info:
        services.answer_question(
            query="保单整理有什么作用？",
            top_n=20,
            top_k=5,
        )

    assert str(exc_info.value) == LOCAL_INDEX_ERROR
    assert "/Users/milan" not in str(exc_info.value)
    assert "secret-token" not in str(exc_info.value)


def test_batch_concurrency_public_name_with_legacy_alias(monkeypatch) -> None:
    monkeypatch.setattr(
        services, "load_env_values", lambda: {"WEB_BATCH_CONCURRENCY": "5"}
    )
    docker = _fake_config()
    docker.milvus_mode = "docker"

    assert services._batch_concurrency is services.batch_concurrency
    assert services.batch_concurrency(_fake_config()) == 1
    assert services.batch_concurrency(docker) == 5


def test_answer_question_holds_lite_lock_during_execution(monkeypatch) -> None:
    _install_rag_stubs(monkeypatch)
    observed = {}

    def fake_answer_query(**kwargs):
        observed["locked"] = services._LITE_ANSWER_LOCK.locked()
        return {"answer": "回答", "citations": [], "evidence_count": 0}

    monkeypatch.setattr(services, "answer_query", fake_answer_query)

    services.answer_question(query="q", top_n=20, top_k=5)

    assert observed["locked"] is True
    assert services._LITE_ANSWER_LOCK.locked() is False


def test_answer_question_skips_lite_lock_for_docker_mode(monkeypatch) -> None:
    _install_rag_stubs(monkeypatch)

    def docker_config():
        config = _fake_config()
        config.milvus_mode = "docker"
        return config

    monkeypatch.setattr(services.RetrievalConfig, "from_env", docker_config)
    observed = {}

    def fake_answer_query(**kwargs):
        observed["locked"] = services._LITE_ANSWER_LOCK.locked()
        return {"answer": "回答", "citations": [], "evidence_count": 0}

    monkeypatch.setattr(services, "answer_query", fake_answer_query)

    services.answer_question(query="q", top_n=20, top_k=5)

    assert observed["locked"] is False


def test_answer_question_serializes_lite_calls(monkeypatch) -> None:
    _install_rag_stubs(monkeypatch)
    entered = threading.Event()

    def fake_answer_query(**kwargs):
        entered.set()
        return {"answer": "回答", "citations": [], "evidence_count": 0}

    monkeypatch.setattr(services, "answer_query", fake_answer_query)

    services._LITE_ANSWER_LOCK.acquire()
    worker = threading.Thread(
        target=lambda: services.answer_question(query="q", top_n=20, top_k=5),
        daemon=True,
    )
    try:
        worker.start()
        # 主线程持锁期间，lite 模式的问答不应进入执行阶段。
        assert entered.wait(0.2) is False
    finally:
        services._LITE_ANSWER_LOCK.release()
    worker.join(timeout=5)
    assert entered.is_set() is True
