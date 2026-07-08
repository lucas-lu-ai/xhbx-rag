import asyncio
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

import xhbx_rag.mcp_server as mcp_server
from xhbx_rag.config import ConfigError
from xhbx_rag.milvus_store import MilvusSearchHit
from xhbx_rag.mcp_server import (
    ConfiguredEvidenceSearcher,
    LOCAL_INDEX_UNAVAILABLE_ERROR,
    UNAVAILABLE_SEARCH_ERROR,
    create_mcp_server,
)
from xhbx_rag.models import RagChunk
from xhbx_rag.rerank import RerankResult


class FakeSearcher:
    def __init__(self, result: dict | None = None, error: Exception | None = None):
        self.result = result or {"results": []}
        self.error = error
        self.calls: list[dict] = []

    def search(
        self,
        *,
        query: str,
        top_n: int,
        top_k: int,
        filters: dict | None = None,
    ) -> dict:
        self.calls.append(
            {"query": query, "top_n": top_n, "top_k": top_k, "filters": filters or {}}
        )
        if self.error is not None:
            raise self.error
        return self.result


class FakeFilterOptionsProvider:
    def __init__(self, result: dict | None = None, error: Exception | None = None):
        self.result = result or {
            "chunk_types": [{"value": "script", "label": "场景话术"}],
            "stages": ["售前"],
            "case_names": ["案例A"],
        }
        self.error = error
        self.calls = 0

    def filter_options(self) -> dict:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def _call_tool(server, name: str, arguments: dict) -> dict:
    blocks = asyncio.run(server.call_tool(name, arguments))
    assert blocks, "工具应返回内容"
    return json.loads(blocks[0].text)


def test_server_registers_expected_tools():
    server = create_mcp_server(searcher=FakeSearcher())
    tools = asyncio.run(server.list_tools())
    tool_names = {tool.name for tool in tools}
    assert tool_names == {"search_knowledge", "retrieval_status", "list_filter_options"}


def test_search_knowledge_exposes_query_and_optional_filters():
    server = create_mcp_server(searcher=FakeSearcher())
    tools = asyncio.run(server.list_tools())
    search_tool = next(tool for tool in tools if tool.name == "search_knowledge")

    properties = search_tool.inputSchema["properties"]
    assert properties["query"] == {"title": "Query", "type": "string"}
    assert properties["chunk_types"]["default"] is None
    assert properties["stage"]["default"] == ""
    assert properties["case_name"]["default"] == ""
    assert search_tool.inputSchema["required"] == ["query"]


def test_search_knowledge_returns_searcher_result():
    expected = {
        "original_query": "怎么处理客户异议",
        "rewritten_query": "客户异议处理方法",
        "intent": "objection_handling",
        "results": [{"chunk_id": "c1", "text": "先共情再澄清"}],
    }
    searcher = FakeSearcher(result=expected)
    server = create_mcp_server(searcher=searcher)

    payload = _call_tool(
        server,
        "search_knowledge",
        {"query": "怎么处理客户异议"},
    )

    assert payload == expected
    assert searcher.calls == [
        {"query": "怎么处理客户异议", "top_n": 20, "top_k": 5, "filters": {}}
    ]


def test_search_knowledge_uses_default_limits_and_strips_query():
    searcher = FakeSearcher()
    server = create_mcp_server(searcher=searcher)

    _call_tool(server, "search_knowledge", {"query": "  高净值客户开拓  "})

    assert searcher.calls == [
        {"query": "高净值客户开拓", "top_n": 20, "top_k": 5, "filters": {}}
    ]


def test_search_knowledge_passes_optional_filters():
    searcher = FakeSearcher()
    server = create_mcp_server(searcher=searcher)

    _call_tool(
        server,
        "search_knowledge",
        {
            "query": "预算异议",
            "chunk_types": ["script", "", "objection_handling"],
            "stage": " 异议处理 ",
            "case_name": " 案例A ",
        },
    )

    assert searcher.calls == [
        {
            "query": "预算异议",
            "top_n": 20,
            "top_k": 5,
            "filters": {
                "chunk_types": ["script", "objection_handling"],
                "stage": "异议处理",
                "case_name": "案例A",
            },
        }
    ]


def test_search_knowledge_rejects_empty_query():
    searcher = FakeSearcher()
    server = create_mcp_server(searcher=searcher)

    with pytest.raises(ToolError, match="问题不能为空"):
        asyncio.run(server.call_tool("search_knowledge", {"query": "   "}))
    assert searcher.calls == []


def test_search_knowledge_masks_internal_error():
    searcher = FakeSearcher(
        error=RuntimeError("Traceback /Users/secret/xhbx.db connection refused")
    )
    server = create_mcp_server(searcher=searcher)

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(server.call_tool("search_knowledge", {"query": "客户经营"}))

    message = str(exc_info.value)
    assert UNAVAILABLE_SEARCH_ERROR in message
    assert "secret" not in message
    assert "Traceback" not in message


def test_search_knowledge_passes_through_safe_config_error():
    searcher = FakeSearcher(error=ConfigError("缺少必要环境变量: API_KEY, BASE_URL"))
    server = create_mcp_server(searcher=searcher)

    with pytest.raises(ToolError, match="缺少必要环境变量: API_KEY, BASE_URL"):
        asyncio.run(server.call_tool("search_knowledge", {"query": "客户经营"}))


def test_search_knowledge_masks_tampered_config_error():
    searcher = FakeSearcher(
        error=ConfigError("缺少必要环境变量: /etc/passwd 泄漏内容")
    )
    server = create_mcp_server(searcher=searcher)

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(server.call_tool("search_knowledge", {"query": "客户经营"}))

    message = str(exc_info.value)
    assert UNAVAILABLE_SEARCH_ERROR in message
    assert "passwd" not in message


def test_search_knowledge_passes_through_local_index_error():
    searcher = FakeSearcher(error=ValueError(LOCAL_INDEX_UNAVAILABLE_ERROR))
    server = create_mcp_server(searcher=searcher)

    with pytest.raises(ToolError, match="本地 Milvus 索引暂时不可用"):
        asyncio.run(server.call_tool("search_knowledge", {"query": "客户经营"}))


def test_retrieval_status_returns_provider_payload():
    expected = {
        "ok": True,
        "milvus_mode": "lite",
        "milvus_target": ".local/milvus/xhbx_rag.db",
        "milvus_collection": "xhbx_sales_chunks",
        "errors": [],
    }
    server = create_mcp_server(
        searcher=FakeSearcher(),
        status_provider=lambda: expected,
    )

    payload = _call_tool(server, "retrieval_status", {})

    assert payload == expected


def test_retrieval_status_masks_provider_error():
    def broken_provider() -> dict:
        raise RuntimeError("内部堆栈 /Users/secret")

    server = create_mcp_server(
        searcher=FakeSearcher(),
        status_provider=broken_provider,
    )

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(server.call_tool("retrieval_status", {}))

    message = str(exc_info.value)
    assert UNAVAILABLE_SEARCH_ERROR in message
    assert "secret" not in message


def test_list_filter_options_returns_provider_payload():
    expected = {
        "chunk_types": [
            {"value": "script", "label": "场景话术"},
            {"value": "objection_handling", "label": "异议处理"},
        ],
        "stages": ["售前", "异议处理"],
        "case_names": ["案例A", "案例B"],
    }
    provider = FakeFilterOptionsProvider(result=expected)
    server = create_mcp_server(
        searcher=FakeSearcher(),
        filter_options_provider=provider,
    )

    payload = _call_tool(server, "list_filter_options", {})

    assert payload == expected
    assert provider.calls == 1


def test_list_filter_options_masks_provider_error():
    provider = FakeFilterOptionsProvider(error=RuntimeError("内部堆栈 /Users/secret"))
    server = create_mcp_server(
        searcher=FakeSearcher(),
        filter_options_provider=provider,
    )

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(server.call_tool("list_filter_options", {}))

    message = str(exc_info.value)
    assert UNAVAILABLE_SEARCH_ERROR in message
    assert "secret" not in message


def test_default_retrieval_status_does_not_require_chat_config(monkeypatch):
    calls = []
    config = type(
        "Config",
        (),
        {
            "milvus_mode": "docker",
            "milvus_uri": "http://localhost:19530",
            "milvus_lite_path": "",
            "milvus_collection": "xhbx_sales_chunks",
            "milvus_course_collection": "xhbx_course_chunks",
        },
    )()

    monkeypatch.setattr(
        mcp_server.RetrievalConfig,
        "from_env",
        classmethod(
            lambda cls, *, require_chat=True: calls.append(require_chat) or config
        ),
    )

    payload = mcp_server._default_status_provider()

    assert calls == [False]
    assert payload["ok"] is True
    assert payload["milvus_collection"] == "xhbx_sales_chunks"


def test_create_default_server_without_injection():
    server = create_mcp_server()
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == {
        "search_knowledge",
        "retrieval_status",
        "list_filter_options",
    }


def test_create_server_uses_default_http_binding():
    server = create_mcp_server(searcher=FakeSearcher())
    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 8000
    assert server.settings.sse_path == "/sse"
    assert server.settings.streamable_http_path == "/mcp"


def test_create_server_accepts_custom_http_binding():
    server = create_mcp_server(
        searcher=FakeSearcher(),
        host="0.0.0.0",
        port=9331,
    )
    assert server.settings.host == "0.0.0.0"
    assert server.settings.port == 9331


def test_create_server_accepts_custom_endpoint_paths():
    server = create_mcp_server(
        searcher=FakeSearcher(),
        sse_path="/mcp/sse",
        streamable_http_path="/knowledge",
    )
    assert server.settings.sse_path == "/mcp/sse"
    assert server.settings.streamable_http_path == "/knowledge"


def test_configured_searcher_skips_chat_model_and_uses_embedding_and_rerank(monkeypatch):
    calls = {"embedding": [], "vector": [], "keyword": [], "rerank": []}

    class FakeEmbeddingClient:
        def __init__(self, *, base_url, api_key, model):
            assert base_url == "https://embedding.example.com/v1"
            assert api_key == "embedding-key"
            assert model == "embedding-model"

        def embed_query(self, text):
            calls["embedding"].append(text)
            return [0.1, 0.2, 0.3]

    class FakeRerankClient:
        def __init__(self, *, base_url, api_key, model):
            assert base_url == "https://rerank.example.com/v1"
            assert api_key == "rerank-key"
            assert model == "rerank-model"

        def rerank(self, query, documents, top_k):
            calls["rerank"].append(
                {"query": query, "documents": documents, "top_k": top_k}
            )
            return [RerankResult(index=1, relevance_score=0.98, text=documents[1])]

    def chunk(chunk_id: str, text: str) -> RagChunk:
        return RagChunk(
            chunk_id=chunk_id,
            chunk_type="script",
            text=text,
            metadata={"case_name": "案例A", "stage": "售前"},
            citations=[],
            source_file="case.sales_insights.json",
        )

    class FakeStore:
        def search(self, *, vector, top_k, filters):
            calls["vector"].append({"vector": vector, "top_k": top_k, "filters": filters})
            return [MilvusSearchHit(chunk=chunk("v1", "向量命中"), score=0.9)]

        def keyword_search(self, *, query, top_k, filters):
            calls["keyword"].append({"query": query, "top_k": top_k, "filters": filters})
            return [MilvusSearchHit(chunk=chunk("k1", "关键词命中"), score=2.0)]

    config = type(
        "Config",
        (),
        {
            "milvus_mode": "docker",
            "embedding_base_url": "https://embedding.example.com/v1",
            "embedding_api_key": "embedding-key",
            "embedding_model_name": "embedding-model",
            "rerank_base_url": "https://rerank.example.com/v1",
            "rerank_api_key": "rerank-key",
            "rerank_model_name": "rerank-model",
        },
    )()

    monkeypatch.setattr(
        mcp_server.RetrievalConfig,
        "from_env",
        classmethod(lambda cls, *, require_chat=True: config),
    )
    monkeypatch.setattr(mcp_server, "EmbeddingClient", FakeEmbeddingClient)
    monkeypatch.setattr(mcp_server, "RerankClient", FakeRerankClient)
    monkeypatch.setattr(mcp_server, "create_retrieval_store", lambda config: FakeStore())

    result = ConfiguredEvidenceSearcher().search(
        query="客户异议怎么处理",
        top_n=20,
        top_k=5,
        filters={"chunk_types": ["script"], "stage": "售前", "case_name": "案例A"},
    )

    assert not hasattr(mcp_server, "QueryUnderstandingAgent")
    assert result["original_query"] == "客户异议怎么处理"
    assert result["rewritten_query"] == "客户异议怎么处理"
    assert result["intent"] == "direct_retrieval"
    assert result["filters"] == {
        "chunk_types": ["script"],
        "stage": "售前",
        "case_name": "案例A",
    }
    assert result["results"][0]["chunk_id"] == "k1"
    assert calls == {
        "embedding": ["客户异议怎么处理"],
        "vector": [
            {
                "vector": [0.1, 0.2, 0.3],
                "top_k": 20,
                "filters": {
                    "chunk_types": ["script"],
                    "stage": "售前",
                    "case_name": "案例A",
                },
            }
        ],
        "keyword": [
            {
                "query": "客户异议怎么处理",
                "top_k": 20,
                "filters": {
                    "chunk_types": ["script"],
                    "stage": "售前",
                    "case_name": "案例A",
                },
            }
        ],
        "rerank": [
            {
                "query": "客户异议怎么处理",
                "documents": ["向量命中", "关键词命中"],
                "top_k": 5,
            }
        ],
    }
