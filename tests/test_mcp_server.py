import asyncio
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from xhbx_rag.config import ConfigError
from xhbx_rag.mcp_server import (
    LOCAL_INDEX_UNAVAILABLE_ERROR,
    UNAVAILABLE_SEARCH_ERROR,
    create_mcp_server,
)


class FakeSearcher:
    def __init__(self, result: dict | None = None, error: Exception | None = None):
        self.result = result or {"results": []}
        self.error = error
        self.calls: list[dict] = []

    def search(self, *, query: str, top_n: int, top_k: int) -> dict:
        self.calls.append({"query": query, "top_n": top_n, "top_k": top_k})
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
    assert tool_names == {"search_knowledge", "retrieval_status"}


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
        {"query": "怎么处理客户异议", "top_n": 10, "top_k": 3},
    )

    assert payload == expected
    assert searcher.calls == [
        {"query": "怎么处理客户异议", "top_n": 10, "top_k": 3}
    ]


def test_search_knowledge_uses_default_limits_and_strips_query():
    searcher = FakeSearcher()
    server = create_mcp_server(searcher=searcher)

    _call_tool(server, "search_knowledge", {"query": "  高净值客户开拓  "})

    assert searcher.calls == [{"query": "高净值客户开拓", "top_n": 20, "top_k": 5}]


def test_search_knowledge_rejects_empty_query():
    searcher = FakeSearcher()
    server = create_mcp_server(searcher=searcher)

    with pytest.raises(ToolError, match="问题不能为空"):
        asyncio.run(server.call_tool("search_knowledge", {"query": "   "}))
    assert searcher.calls == []


@pytest.mark.parametrize(
    ("top_n", "top_k", "message"),
    [
        (0, 5, "top_n 必须在 1 到 100 之间"),
        (101, 5, "top_n 必须在 1 到 100 之间"),
        (20, 0, "top_k 必须在 1 到 20 之间"),
        (20, 21, "top_k 必须在 1 到 20 之间"),
        (5, 10, "top_k 不能大于 top_n"),
    ],
)
def test_search_knowledge_rejects_bad_limits(top_n, top_k, message):
    searcher = FakeSearcher()
    server = create_mcp_server(searcher=searcher)

    with pytest.raises(ToolError, match=message):
        asyncio.run(
            server.call_tool(
                "search_knowledge",
                {"query": "客户经营", "top_n": top_n, "top_k": top_k},
            )
        )
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


def test_create_default_server_without_injection():
    server = create_mcp_server()
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == {"search_knowledge", "retrieval_status"}
