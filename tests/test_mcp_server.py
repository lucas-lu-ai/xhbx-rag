import asyncio
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session

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
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple):
        blocks, structured_content = result
        assert blocks, "工具应保留兼容文本内容"
        assert isinstance(structured_content, dict)
        return structured_content
    assert result, "工具应返回内容"
    return json.loads(result[0].text)


def _call_structured_tool(server, name: str, arguments: dict) -> tuple[list, dict]:
    result = asyncio.run(server.call_tool(name, arguments))
    assert isinstance(result, tuple), "工具应返回兼容文本和 structuredContent"
    blocks, structured_content = result
    assert blocks
    assert isinstance(structured_content, dict)
    return list(blocks), structured_content


def test_server_registers_expected_tools():
    server = create_mcp_server(searcher=FakeSearcher())
    tools = asyncio.run(server.list_tools())
    tool_names = {tool.name for tool in tools}
    assert tool_names == {"kb_search_knowledge"}


def test_server_can_expose_legacy_tools_for_rollback():
    server = create_mcp_server(searcher=FakeSearcher(), expose_legacy_tools=True)
    tools = asyncio.run(server.list_tools())
    tool_names = {tool.name for tool in tools}
    assert tool_names == {
        "kb_search_knowledge",
        "search_knowledge",
        "retrieval_status",
        "list_filter_options",
    }


def test_server_can_use_legacy_tool_profile():
    server = create_mcp_server(searcher=FakeSearcher(), tool_profile="legacy")
    tools = asyncio.run(server.list_tools())
    tool_names = {tool.name for tool in tools}
    assert tool_names == {"search_knowledge", "retrieval_status", "list_filter_options"}


def test_server_can_use_both_tool_profile():
    server = create_mcp_server(searcher=FakeSearcher(), tool_profile="both")
    tools = asyncio.run(server.list_tools())
    tool_names = {tool.name for tool in tools}
    assert tool_names == {
        "kb_search_knowledge",
        "search_knowledge",
        "retrieval_status",
        "list_filter_options",
    }


def test_server_instructions_follow_tool_profile():
    legacy_server = create_mcp_server(searcher=FakeSearcher(), tool_profile="legacy")
    both_server = create_mcp_server(searcher=FakeSearcher(), tool_profile="both")

    assert "search_knowledge" in legacy_server.instructions
    assert "kb_search_knowledge" not in legacy_server.instructions
    assert "kb_search_knowledge" in both_server.instructions
    assert "search_knowledge" in both_server.instructions


def test_tool_profile_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_TOOL_PROFILE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("MCP_TOOL_PROFILE=legacy\n", encoding="utf-8")

    assert mcp_server._tool_profile_from_env(env_file=env_file) == "legacy"


def test_tool_profile_env_overrides_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("MCP_TOOL_PROFILE=legacy\n", encoding="utf-8")
    monkeypatch.setenv("MCP_TOOL_PROFILE", "both")

    assert mcp_server._tool_profile_from_env(env_file=env_file) == "both"


def test_tool_profile_rejects_invalid_value(tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_TOOL_PROFILE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("MCP_TOOL_PROFILE=old\n", encoding="utf-8")

    with pytest.raises(ValueError, match="MCP_TOOL_PROFILE"):
        mcp_server._tool_profile_from_env(env_file=env_file)


def test_kb_search_knowledge_exposes_documented_parameters():
    server = create_mcp_server(searcher=FakeSearcher())
    tools = asyncio.run(server.list_tools())
    search_tool = next(tool for tool in tools if tool.name == "kb_search_knowledge")

    properties = search_tool.inputSchema["properties"]
    assert properties["query"] == {"title": "Query", "type": "string"}
    assert "kbId" not in properties
    assert properties["primaryDomains"]["type"] == "array"
    assert properties["primaryDomains"]["items"] == {
        "type": "string",
        "enum": list(mcp_server.CANONICAL_DOMAINS),
    }
    assert properties["primaryDomains"]["minItems"] == 0
    assert properties["primaryDomains"]["maxItems"] == 7
    assert properties["knowledgeTypes"]["default"] is None
    assert properties["retrievalMode"]["default"] == "HYBRID"
    assert properties["hybridWeights"]["default"] is None
    assert properties["topK"]["default"] == 10
    assert properties["includeDetails"]["default"] is False
    assert search_tool.inputSchema["required"] == ["query", "primaryDomains"]
    for domain in mcp_server.CANONICAL_DOMAINS:
        assert domain in search_tool.description
    assert "一个或多个最相关领域" in search_tool.description
    assert "无法匹配现有体系时传入空数组" in search_tool.description
    assert search_tool.outputSchema is not None
    assert search_tool.outputSchema["type"] == "object"


def test_kb_search_knowledge_returns_structured_content_with_text_fallback():
    expected = {
        "success": True,
        "data": [
            {
                "docId": "pptx:案例A.pptx",
                "knowledgeType": "SLICE",
                "title": "切片",
                "content": "完整正文",
            }
        ],
        "errorCode": None,
        "errorMessage": None,
    }
    server = create_mcp_server(
        searcher=FakeSearcher(
            result={
                "results": [
                    {
                        "text": "完整正文",
                        "metadata": {
                            "primary_domain": "销售技能",
                            "domain_tags": ["销售技能", "客户经营"],
                        },
                        "citations": [{"source_id": "pptx:案例A.pptx"}],
                    }
                ]
            }
        )
    )

    blocks, structured_content = _call_structured_tool(
        server,
        "kb_search_knowledge",
        {"query": "客户经营", "primaryDomains": ["客户经营"]},
    )

    assert structured_content == expected
    assert json.loads(blocks[0].text) == expected


def test_kb_search_knowledge_protocol_result_contains_structured_content():
    expected = {
        "success": True,
        "data": [
            {
                "docId": "pptx:案例A.pptx",
                "knowledgeType": "SLICE",
                "title": "切片",
                "content": "完整正文",
            }
        ],
        "errorCode": None,
        "errorMessage": None,
    }
    server = create_mcp_server(
        searcher=FakeSearcher(
            result={
                "results": [
                    {
                        "text": "完整正文",
                        "metadata": {
                            "primary_domain": "销售技能",
                            "domain_tags": ["销售技能", "客户经营"],
                        },
                        "citations": [{"source_id": "pptx:案例A.pptx"}],
                    }
                ]
            }
        )
    )

    async def call_tool():
        async with create_connected_server_and_client_session(server) as session:
            return await session.call_tool(
                "kb_search_knowledge",
                {"query": "客户经营", "primaryDomains": ["客户经营"]},
            )

    result = asyncio.run(call_tool())

    assert result.structuredContent == expected
    assert result.content
    assert json.loads(result.content[0].text) == expected
    assert result.isError is False


def test_kb_search_knowledge_returns_wrapped_slice_results():
    searcher = FakeSearcher(
        result={
            "results": [
                {
                    "chunk_id": "c1",
                    "chunk_type": "script",
                    "text": "先共情再澄清客户预算异议",
                    "score": 0.4,
                    "rerank_score": 0.98,
                    "metadata": {
                        "tag_paths": ["异议处理/预算"],
                        "parent_id": "p1",
                        "title_path": ["案例A", "预算异议"],
                        "primary_domain": "销售技能",
                        "domain_tags": ["销售技能", "客户经营"],
                    },
                    "citations": [{"source_path": "案例A/a.txt"}],
                }
            ]
        }
    )
    server = create_mcp_server(searcher=searcher)

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {
            "query": "预算异议怎么处理",
            "primaryDomains": ["销售技能"],
            "topK": 5,
            "includeDetails": True,
        },
    )

    assert payload["success"] is True
    assert payload["errorCode"] is None
    assert payload["errorMessage"] is None
    assert payload["data"] == [
        {
            "id": "c1",
            "knowledgeType": "SLICE",
            "score": 0.98,
            "primaryDomain": "销售技能",
            "domainTags": ["销售技能", "客户经营"],
            "tags": ["异议处理/预算"],
            "qa": None,
            "slice": {
                "content": "先共情再澄清客户预算异议",
                "fullContent": "先共情再澄清客户预算异议",
                "contentTruncated": False,
                "sliceType": "script",
                "parentId": "p1",
                "titlePath": ["案例A", "预算异议"],
                "parentSliceContext": None,
                "citations": [{"source_path": "案例A/a.txt"}],
            },
            "knowledgePoint": None,
        }
    ]
    assert searcher.calls == [
        {
            "query": "预算异议怎么处理",
            "top_n": 20,
            "top_k": 5,
            "filters": {"primary_domains": ["销售技能"]},
        }
    ]


def test_kb_search_knowledge_returns_full_results_when_details_enabled():
    searcher = FakeSearcher(
        result={"results": [{"chunk_id": "c1", "text": "完整正文"}]}
    )
    server = create_mcp_server(searcher=searcher)

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {
            "query": "客户经营",
            "primaryDomains": ["客户经营"],
            "includeDetails": True,
        },
    )

    assert payload["data"][0]["id"] == "c1"
    assert payload["data"][0]["slice"]["fullContent"] == "完整正文"


@pytest.mark.parametrize("details_argument", [{}, {"includeDetails": False}])
def test_kb_search_knowledge_returns_only_compact_fields_by_default_or_when_disabled(
    details_argument,
):
    searcher = FakeSearcher(
        result={
            "results": [
                {
                    "chunk_id": "c1",
                    "text": "完整正文",
                    "metadata": {
                        "primary_domain": "客户经营",
                        "domain_tags": ["客户经营"],
                    },
                    "citations": [
                        {
                            "source_id": "pptx:案例A.pptx",
                            "source_path": "案例A/a.pptx",
                        },
                        {
                            "source_id": "docx:案例B.docx",
                            "source_path": "案例B/b.docx",
                        },
                    ],
                }
            ]
        }
    )
    server = create_mcp_server(searcher=searcher)

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {
            "query": "客户经营",
            "primaryDomains": ["客户经营"],
            **details_argument,
        },
    )

    assert payload["data"] == [
        {
            "docId": "pptx:案例A.pptx",
            "knowledgeType": "SLICE",
            "title": "切片",
            "content": "完整正文",
        }
    ]


def test_kb_search_knowledge_compact_results_do_not_exceed_top_k():
    server = create_mcp_server(
        searcher=FakeSearcher(
            result={
                "results": [
                    {
                        "text": f"正文{index}",
                        "citations": [{"source_id": f"doc:{index}"}],
                    }
                    for index in range(3)
                ]
            }
        )
    )

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {
            "query": "客户经营",
            "primaryDomains": ["客户经营"],
            "topK": 2,
        },
    )

    assert payload["data"] == [
        {
            "docId": "doc:0",
            "knowledgeType": "SLICE",
            "title": "切片",
            "content": "正文0",
        },
        {
            "docId": "doc:1",
            "knowledgeType": "SLICE",
            "title": "切片",
            "content": "正文1",
        },
    ]


@pytest.mark.parametrize(
    "citations",
    [None, [], ["不是对象"], [{}]],
)
def test_compact_kb_search_results_use_empty_doc_id_for_missing_citation(
    citations,
):
    raw = {"text": "正文"}
    if citations is not None:
        raw["citations"] = citations
    server = create_mcp_server(searcher=FakeSearcher(result={"results": [raw]}))

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {
            "query": "客户经营",
            "primaryDomains": ["客户经营"],
            "includeDetails": False,
        },
    )

    assert payload["data"] == [
        {
            "docId": "",
            "knowledgeType": "SLICE",
            "title": "切片",
            "content": "正文",
        }
    ]


def test_compact_kb_search_results_use_empty_content_when_text_is_missing():
    server = create_mcp_server(
        searcher=FakeSearcher(result={"results": [{"citations": []}]})
    )

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {
            "query": "客户经营",
            "primaryDomains": ["客户经营"],
            "includeDetails": False,
        },
    )

    assert payload["data"] == [
        {
            "docId": "",
            "knowledgeType": "SLICE",
            "title": "切片",
            "content": "",
        }
    ]


@pytest.mark.parametrize(
    "citation",
    [{}, {"source_id": None}, {"source_id": ""}],
)
def test_compact_kb_search_results_use_empty_doc_id_when_first_citation_source_id_is_missing_or_empty(
    citation,
):
    server = create_mcp_server(
        searcher=FakeSearcher(
            result={"results": [{"text": "正文", "citations": [citation]}]}
        )
    )

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {
            "query": "客户经营",
            "primaryDomains": ["客户经营"],
            "includeDetails": False,
        },
    )

    assert payload["data"] == [
        {
            "docId": "",
            "knowledgeType": "SLICE",
            "title": "切片",
            "content": "正文",
        }
    ]


def test_kb_search_knowledge_compact_results_do_not_leak_dirty_metadata():
    server = create_mcp_server(
        searcher=FakeSearcher(
            result={
                "results": [
                    {
                        "chunk_id": "c1",
                        "text": "正文",
                        "metadata": {"domain_tags": "销售技能"},
                        "citations": [],
                    }
                ]
            }
        )
    )

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {"query": "客户经营", "primaryDomains": ["客户经营"]},
    )

    assert payload["data"] == [
        {
            "docId": "",
            "knowledgeType": "SLICE",
            "title": "切片",
            "content": "正文",
        }
    ]


def test_kb_search_knowledge_filters_by_normalized_primary_domains():
    searcher = FakeSearcher()
    server = create_mcp_server(searcher=searcher)

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {
            "query": "  客户担心保费太高怎么沟通  ",
            "primaryDomains": ["销售技能", " 客户经营 ", "销售技能"],
        },
    )

    assert payload["success"] is True
    assert searcher.calls == [
        {
            "query": "客户担心保费太高怎么沟通",
            "top_n": 20,
            "top_k": 10,
            "filters": {"primary_domains": ["销售技能", "客户经营"]},
        }
    ]


def test_kb_search_knowledge_accepts_all_canonical_domains():
    searcher = FakeSearcher()
    server = create_mcp_server(searcher=searcher)

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {
            "query": "综合问题",
            "primaryDomains": list(mcp_server.CANONICAL_DOMAINS),
        },
    )

    assert payload["success"] is True
    assert searcher.calls[0]["filters"] == {
        "primary_domains": list(mcp_server.CANONICAL_DOMAINS)
    }


def test_kb_search_knowledge_empty_primary_domains_searches_all_documents():
    searcher = FakeSearcher()
    server = create_mcp_server(searcher=searcher)

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {"query": "无法匹配现有体系的问题", "primaryDomains": []},
    )

    assert payload["success"] is True
    assert searcher.calls == [
        {
            "query": "无法匹配现有体系的问题",
            "top_n": 20,
            "top_k": 10,
            "filters": {},
        }
    ]


def test_kb_search_knowledge_returns_empty_data_when_slice_not_requested():
    searcher = FakeSearcher()
    server = create_mcp_server(searcher=searcher)

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {
            "query": "预算异议",
            "primaryDomains": ["销售技能"],
            "knowledgeTypes": ["QA"],
        },
    )

    assert payload == {
        "success": True,
        "data": [],
        "errorCode": None,
        "errorMessage": None,
    }
    assert searcher.calls == []


def test_kb_search_knowledge_returns_parameter_error_for_empty_query():
    searcher = FakeSearcher()
    server = create_mcp_server(searcher=searcher)

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {"query": "   ", "primaryDomains": ["销售技能"]},
    )

    assert payload["success"] is False
    assert payload["data"] is None
    assert payload["errorCode"] == "10004"
    assert payload["errorMessage"] == "参数错误: query 不能为空"
    assert searcher.calls == []


@pytest.mark.parametrize(
    "primary_domains",
    ["销售技能", ["不存在的领域"], ["销售技能", 1], ["销售技能"] * 8],
)
def test_kb_search_knowledge_wraps_invalid_primary_domains(primary_domains):
    searcher = FakeSearcher()
    server = create_mcp_server(searcher=searcher)

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {"query": "客户经营", "primaryDomains": primary_domains},
    )

    assert payload == {
        "success": False,
        "data": None,
        "errorCode": "10004",
        "errorMessage": "参数错误: primaryDomains 必须是由 0 到 7 个合法一级领域组成的数组",
    }
    assert searcher.calls == []


def test_kb_search_knowledge_rejects_old_kb_id_only_call():
    server = create_mcp_server(searcher=FakeSearcher())

    with pytest.raises(ToolError, match="primaryDomains"):
        _call_tool(
            server,
            "kb_search_knowledge",
            {"query": "客户经营", "kbId": 1},
        )


def test_kb_search_knowledge_returns_parameter_error_for_unsupported_mode():
    server = create_mcp_server(searcher=FakeSearcher())

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {
            "query": "客户经营",
            "primaryDomains": ["客户经营"],
            "retrievalMode": "VECTOR",
        },
    )

    assert payload["success"] is False
    assert payload["data"] is None
    assert payload["errorCode"] == "10004"
    assert payload["errorMessage"] == (
        "参数错误: retrievalMode 暂时仅支持 HYBRID"
    )


def test_kb_search_knowledge_returns_parameter_error_for_invalid_top_k():
    server = create_mcp_server(searcher=FakeSearcher())

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {
            "query": "客户经营",
            "primaryDomains": ["客户经营"],
            "topK": 51,
        },
    )

    assert payload["success"] is False
    assert payload["data"] is None
    assert payload["errorCode"] == "10004"
    assert payload["errorMessage"] == "参数错误: topK 必须在 1 到 50 之间"


def test_kb_search_knowledge_masks_internal_error_in_wrapped_response():
    searcher = FakeSearcher(
        error=RuntimeError("Traceback /Users/secret/xhbx.db connection refused")
    )
    server = create_mcp_server(searcher=searcher)

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {"query": "客户经营", "primaryDomains": ["客户经营"]},
    )

    assert payload["success"] is False
    assert payload["data"] is None
    assert payload["errorCode"] == "500"
    assert payload["errorMessage"] == UNAVAILABLE_SEARCH_ERROR


def test_search_knowledge_exposes_query_and_optional_filters():
    server = create_mcp_server(searcher=FakeSearcher(), expose_legacy_tools=True)
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
    server = create_mcp_server(searcher=searcher, expose_legacy_tools=True)

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
    server = create_mcp_server(searcher=searcher, expose_legacy_tools=True)

    _call_tool(server, "search_knowledge", {"query": "  高净值客户开拓  "})

    assert searcher.calls == [
        {"query": "高净值客户开拓", "top_n": 20, "top_k": 5, "filters": {}}
    ]


def test_search_knowledge_passes_optional_filters():
    searcher = FakeSearcher()
    server = create_mcp_server(searcher=searcher, expose_legacy_tools=True)

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
    server = create_mcp_server(searcher=searcher, expose_legacy_tools=True)

    with pytest.raises(ToolError, match="问题不能为空"):
        asyncio.run(server.call_tool("search_knowledge", {"query": "   "}))
    assert searcher.calls == []


def test_search_knowledge_masks_internal_error():
    searcher = FakeSearcher(
        error=RuntimeError("Traceback /Users/secret/xhbx.db connection refused")
    )
    server = create_mcp_server(searcher=searcher, expose_legacy_tools=True)

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(server.call_tool("search_knowledge", {"query": "客户经营"}))

    message = str(exc_info.value)
    assert UNAVAILABLE_SEARCH_ERROR in message
    assert "secret" not in message
    assert "Traceback" not in message


def test_search_knowledge_passes_through_safe_config_error():
    searcher = FakeSearcher(error=ConfigError("缺少必要环境变量: API_KEY, BASE_URL"))
    server = create_mcp_server(searcher=searcher, expose_legacy_tools=True)

    with pytest.raises(ToolError, match="缺少必要环境变量: API_KEY, BASE_URL"):
        asyncio.run(server.call_tool("search_knowledge", {"query": "客户经营"}))


def test_search_knowledge_masks_tampered_config_error():
    searcher = FakeSearcher(
        error=ConfigError("缺少必要环境变量: /etc/passwd 泄漏内容")
    )
    server = create_mcp_server(searcher=searcher, expose_legacy_tools=True)

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(server.call_tool("search_knowledge", {"query": "客户经营"}))

    message = str(exc_info.value)
    assert UNAVAILABLE_SEARCH_ERROR in message
    assert "passwd" not in message


def test_search_knowledge_passes_through_local_index_error():
    searcher = FakeSearcher(error=ValueError(LOCAL_INDEX_UNAVAILABLE_ERROR))
    server = create_mcp_server(searcher=searcher, expose_legacy_tools=True)

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
        expose_legacy_tools=True,
    )

    payload = _call_tool(server, "retrieval_status", {})

    assert payload == expected


def test_retrieval_status_masks_provider_error():
    def broken_provider() -> dict:
        raise RuntimeError("内部堆栈 /Users/secret")

    server = create_mcp_server(
        searcher=FakeSearcher(),
        status_provider=broken_provider,
        expose_legacy_tools=True,
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
        expose_legacy_tools=True,
    )

    payload = _call_tool(server, "list_filter_options", {})

    assert payload == expected
    assert provider.calls == 1


def test_list_filter_options_masks_provider_error():
    provider = FakeFilterOptionsProvider(error=RuntimeError("内部堆栈 /Users/secret"))
    server = create_mcp_server(
        searcher=FakeSearcher(),
        filter_options_provider=provider,
        expose_legacy_tools=True,
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
    assert {tool.name for tool in tools} == {"kb_search_knowledge"}


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
