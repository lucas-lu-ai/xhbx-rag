# MCP Primary Domain Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将默认 MCP 检索契约从 `kbId` 知识库选择切换为服务器问答智能体必传的七类一级领域 `primaryDomains`，并在统一 collection 内按 `primary_domain` 硬过滤。

**Architecture:** 服务器问答智能体负责用自身大模型选择一级领域，MCP 仅校验调用参数并把 `primaryDomains` 转为内部 `primary_domains` 过滤条件。现有 embedding、向量/关键词召回、RRF 和 rerank 链路保持不变；精简与完整响应都从 chunk metadata 回传领域信息。

**Tech Stack:** Python 3.12、FastMCP、Pydantic、Milvus/PyMilvus、pytest、POSIX shell、Markdown

## Global Constraints

- 一级领域只能是：`产品知识`、`合规与风控`、`销售技能`、`客户经营`、`行业与公司`、`个人成长`、`组织发展`。
- 默认 `MCP_TOOL_PROFILE=kb` 只注册 `kb_search_knowledge`；`legacy` 和 `both` profile 继续保留旧工具。
- `query` 与 `primaryDomains` 都是必填参数；不保留 `kbId` 兼容分支，也不注册 `kb_list_knowledge_bases`。
- MCP 不调用 chat/completions，不调用 `infer_query_domains`，不按 `source_kind` 过滤。
- `primaryDomains` 非法时返回统一包装的 `10004`；检索内部异常继续返回安全化的 `500`。
- 不重新切片、不重新生成 embedding、不重新入库、不修改 Milvus collection schema。
- 保留 `knowledgeTypes`、`retrievalMode`、`hybridWeights`、`topK`、`includeDetails` 的现有行为。

---

### Task 1: Replace the kbId Tool Contract with Required Primary Domains

**Files:**
- Modify: `src/xhbx_rag/mcp_server.py:14-445`
- Modify: `tests/test_mcp_server.py:77-620`

**Interfaces:**
- Consumes: `xhbx_rag.knowledge_domain.CANONICAL_DOMAINS: tuple[str, ...]` and `EvidenceSearcher.search(*, query: str, top_n: int, top_k: int, filters: dict | None = None) -> dict`.
- Produces: `kb_search_knowledge(query: str, primaryDomains: PrimaryDomainsInput, knowledgeTypes: list[str] | None = None, retrievalMode: str = "HYBRID", hybridWeights: dict[str, Any] | None = None, topK: int = 10, includeDetails: bool = False) -> dict[str, Any]`, `_normalize_primary_domains(value: Any) -> list[str]`, and search filters shaped as `{"primary_domains": list[str]}`.

- [ ] **Step 1: Write failing registration and schema tests**

Replace the default/new-profile tool-name expectations and the old list-tool test with assertions equivalent to:

```python
def test_default_profile_exposes_only_primary_domain_search():
    server = create_mcp_server(searcher=FakeSearcher())
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == {"kb_search_knowledge"}


def test_both_profile_exposes_new_search_and_legacy_tools():
    server = create_mcp_server(searcher=FakeSearcher(), tool_profile="both")
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == {
        "kb_search_knowledge",
        "search_knowledge",
        "retrieval_status",
        "list_filter_options",
    }


def test_kb_search_knowledge_requires_primary_domains_in_schema():
    server = create_mcp_server(searcher=FakeSearcher())
    tools = asyncio.run(server.list_tools())
    search_tool = next(tool for tool in tools if tool.name == "kb_search_knowledge")
    properties = search_tool.inputSchema["properties"]

    assert "kbId" not in properties
    assert properties["primaryDomains"]["type"] == "array"
    assert properties["primaryDomains"]["items"] == {
        "type": "string",
        "enum": list(mcp_server.CANONICAL_DOMAINS),
    }
    assert search_tool.inputSchema["required"] == ["query", "primaryDomains"]
    for domain in mcp_server.CANONICAL_DOMAINS:
        assert domain in search_tool.description
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run:

```bash
uv run pytest tests/test_mcp_server.py -k 'profile or exposes_documented_parameters or requires_primary_domains' -v
```

Expected: FAIL because `kb_list_knowledge_bases` is still registered and the schema still requires `kbId`.

- [ ] **Step 3: Write failing filtering and validation tests**

Add tests that invoke the tool through the existing `_call_tool` helper:

```python
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
    assert searcher.calls == [{
        "query": "客户担心保费太高怎么沟通",
        "top_n": 20,
        "top_k": 10,
        "filters": {"primary_domains": ["销售技能", "客户经营"]},
    }]


def test_kb_search_knowledge_accepts_all_canonical_domains():
    searcher = FakeSearcher()
    server = create_mcp_server(searcher=searcher)
    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {"query": "综合问题", "primaryDomains": list(mcp_server.CANONICAL_DOMAINS)},
    )
    assert payload["success"] is True
    assert searcher.calls[0]["filters"] == {
        "primary_domains": list(mcp_server.CANONICAL_DOMAINS)
    }


@pytest.mark.parametrize(
    "primary_domains",
    [[], "销售技能", ["不存在的领域"], ["销售技能", 1]],
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
        "errorMessage": "参数错误: primaryDomains 必须包含 1 到 7 个合法一级领域",
    }
    assert searcher.calls == []
```

Update every remaining new-tool test call to pass a valid `"primaryDomains": ["销售技能"]`; delete the old unknown-`kbId` permission test and replace the course-`kbId` mapping assertion with the `primary_domains` filtering assertion above. Keep all existing assertions for `knowledgeTypes`, `retrievalMode`, `topK`, structured content, and safe `500` errors.

- [ ] **Step 4: Run the new behavior tests to verify they fail**

Run:

```bash
uv run pytest tests/test_mcp_server.py -k 'primary_domain or primary_domains' -v
```

Expected: FAIL because `primaryDomains` is not accepted and search still uses `chunk_types` derived from `kbId`.

- [ ] **Step 5: Implement the minimal new tool contract**

In `src/xhbx_rag/mcp_server.py`, import `Annotated`, `pydantic.Field`, and `CANONICAL_DOMAINS`, then define a schema-documented runtime input that still lets the business function wrap malformed values:

```python
from typing import Annotated, Any, Callable, Mapping, Protocol, Sequence

from pydantic import Field

from .knowledge_domain import CANONICAL_DOMAINS

PRIMARY_DOMAINS_ERROR = (
    "参数错误: primaryDomains 必须包含 1 到 7 个合法一级领域"
)
PrimaryDomainsInput = Annotated[
    Any,
    Field(
        description="一级领域数组，仅允许七类固定标签。",
        json_schema_extra={
            "type": "array",
            "items": {"type": "string", "enum": list(CANONICAL_DOMAINS)},
            "minItems": 1,
            "maxItems": 7,
        },
    ),
]
```

Replace the new tool signature and filter construction:

```python
def kb_search_knowledge(
    query: str,
    primaryDomains: PrimaryDomainsInput,
    knowledgeTypes: list[str] | None = None,
    retrievalMode: str = SUPPORTED_KB_RETRIEVAL_MODE,
    hybridWeights: dict[str, Any] | None = None,
    topK: int = DEFAULT_KB_TOP_K,
    includeDetails: bool = False,
) -> dict[str, Any]:
    stripped_query = str(query or "").strip()
    if not stripped_query:
        return _mcp_error("10004", "参数错误: query 不能为空")

    try:
        primary_domains = _normalize_primary_domains(primaryDomains)
        top_k = _normalize_kb_top_k(topK)
        retrieval_mode = str(retrievalMode or "").strip().upper()
        if retrieval_mode != SUPPORTED_KB_RETRIEVAL_MODE:
            return _mcp_error(
                "10004",
                "参数错误: retrievalMode 暂时仅支持 HYBRID",
            )
        if hybridWeights is not None and not isinstance(hybridWeights, dict):
            return _mcp_error("10004", "参数错误: hybridWeights 必须为对象")
        knowledge_types = _normalize_knowledge_types(knowledgeTypes)
    except (TypeError, ValueError) as exc:
        return _mcp_error("10004", str(exc))

    filters = {"primary_domains": primary_domains}
```

Add deterministic validation:

```python
def _normalize_primary_domains(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= len(CANONICAL_DOMAINS):
        raise ValueError(PRIMARY_DOMAINS_ERROR)
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(PRIMARY_DOMAINS_ERROR)
        domain = item.strip()
        if domain not in CANONICAL_DOMAINS:
            raise ValueError(PRIMARY_DOMAINS_ERROR)
        if domain not in normalized:
            normalized.append(domain)
    if not normalized:
        raise ValueError(PRIMARY_DOMAINS_ERROR)
    return normalized
```

Remove `KB_CASE_ID`, `KB_COURSE_ID`, `VISIBLE_KNOWLEDGE_BASES`, `CASE_KB_CHUNK_TYPES`, `COURSE_KB_CHUNK_TYPES`, `_kb_filters`, the nested `kb_list_knowledge_bases` function, and its registration. Rewrite `KB_SERVER_INSTRUCTIONS`, `BOTH_SERVER_INSTRUCTIONS`, and the `kb_search_knowledge` description so they require the caller to choose from all seven named labels; do not import or call `infer_query_domains`.

- [ ] **Step 6: Run the MCP server test file**

Run:

```bash
uv run pytest tests/test_mcp_server.py -v
```

Expected: all tests in `tests/test_mcp_server.py` PASS.

- [ ] **Step 7: Commit the contract change**

```bash
git add src/xhbx_rag/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: filter MCP search by primary domain"
```

---

### Task 2: Return Primary Domain Metadata in Compact and Detailed Results

**Files:**
- Modify: `src/xhbx_rag/mcp_server.py:455-530`
- Modify: `tests/test_mcp_server.py:200-490`

**Interfaces:**
- Consumes: search results whose item metadata may contain `primary_domain: str` and `domain_tags: list[str]`.
- Produces: `_normalize_domain_tags(value: Any) -> list[str]`; compact items with `primaryDomain`/`domainTags`; full items with the same two top-level fields.

- [ ] **Step 1: Write failing compact/full response tests**

Extend compact and full fixtures so their raw result contains:

```python
"metadata": {
    "primary_domain": "销售技能",
    "domain_tags": ["销售技能", "客户经营"],
}
```

Assert compact output includes:

```python
{
    "docId": "pptx:案例A.pptx",
    "knowledgeType": "SLICE",
    "title": "切片",
    "content": "完整正文",
    "primaryDomain": "销售技能",
    "domainTags": ["销售技能", "客户经营"],
}
```

Assert full output has the same two fields at the result top level. Add a dirty-data regression case:

```python
def test_kb_search_knowledge_uses_safe_domain_defaults_for_dirty_metadata():
    result = {"results": [{
        "chunk_id": "c1",
        "text": "正文",
        "metadata": {"domain_tags": "销售技能"},
        "citations": [],
    }]}
    payload = _call_tool(
        create_mcp_server(searcher=FakeSearcher(result=result)),
        "kb_search_knowledge",
        {"query": "客户经营", "primaryDomains": ["客户经营"]},
    )
    assert payload["data"][0]["primaryDomain"] == ""
    assert payload["data"][0]["domainTags"] == []
```

- [ ] **Step 2: Run the response tests to verify they fail**

Run:

```bash
uv run pytest tests/test_mcp_server.py -k 'compact or details or domain_defaults' -v
```

Expected: FAIL because formatted results do not yet contain `primaryDomain` and `domainTags`.

- [ ] **Step 3: Implement safe domain metadata formatting**

Add:

```python
def _normalize_domain_tags(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return []
    return [item for item in value if item]
```

In both formatters, normalize metadata before building the output and add:

```python
"primaryDomain": str(metadata.get("primary_domain") or ""),
"domainTags": _normalize_domain_tags(metadata.get("domain_tags")),
```

Change `_format_compact_kb_search_results` return typing from `list[dict[str, str]]` to `list[dict[str, Any]]`, because `domainTags` is a list.

- [ ] **Step 4: Run focused and full MCP tests**

Run:

```bash
uv run pytest tests/test_mcp_server.py -v
```

Expected: all tests PASS, including structuredContent/text fallback equivalence and both response modes.

- [ ] **Step 5: Commit the response change**

```bash
git add src/xhbx_rag/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: expose domain metadata in MCP results"
```

---

### Task 3: Update the Test Script and Caller-Facing Documentation

**Files:**
- Modify: `scripts/test_mcp.sh:1-160`
- Modify: `tests/test_docker_deployment.py:190-210`
- Modify: `.env.mcp.example:29-36`
- Modify: `README.md:338-380`
- Modify: `docs/知识库 MCP Tool 文档.md:1-140`
- Modify: `docs/私有化部署资源文档.md:138-170,400-410`

**Interfaces:**
- Consumes: `PRIMARY_DOMAINS_JSON` environment variable containing a JSON array, defaulting to `["销售技能"]`.
- Produces: a curl smoke call whose `arguments` contains `query`, `primaryDomains`, and `topK`, plus an exact server-Agent prompt/payload example for the seven-domain contract.

- [ ] **Step 1: Write the failing deployment-script contract test**

Replace `test_mcp_test_script_covers_kb_list_and_optional_search` expectations with:

```python
def test_mcp_test_script_covers_primary_domain_search():
    script = read_repo_file("scripts/test_mcp.sh")

    assert 'MCP_URL="${MCP_URL:-http://127.0.0.1:${MCP_PORT:-9331}/mcp}"' in script
    assert 'PRIMARY_DOMAINS_JSON="${PRIMARY_DOMAINS_JSON:-[\\"销售技能\\"]}"' in script
    assert '"name":"kb_list_knowledge_bases"' not in script
    assert '\\"name\\":\\"kb_search_knowledge\\"' in script
    assert '\\"primaryDomains\\":$PRIMARY_DOMAINS_JSON' in script
    assert '\\"kbId\\"' not in script
    assert '\\"topK\\":$TOP_K' in script
```

- [ ] **Step 2: Run the script contract test to verify it fails**

Run:

```bash
uv run pytest tests/test_docker_deployment.py::test_mcp_test_script_covers_primary_domain_search -v
```

Expected: FAIL because the script still calls `kb_list_knowledge_bases` and injects `kbId`.

- [ ] **Step 3: Update the POSIX shell smoke test**

Replace `KB_ID` with:

```sh
PRIMARY_DOMAINS_JSON="${PRIMARY_DOMAINS_JSON:-[\"销售技能\"]}"
```

For `kb|both`, make request id `3` the search call when a query is present, and pass:

```json
"arguments":{
  "query":"$escaped_query",
  "primaryDomains":$PRIMARY_DOMAINS_JSON,
  "topK":$TOP_K
}
```

Do not call `kb_list_knowledge_bases`. Preserve `tools/list`, legacy `retrieval_status`, legacy `search_knowledge`, session handling, query escaping, and curl flags. Update the no-query usage hint to:

```sh
echo "如需检索测试：PRIMARY_DOMAINS_JSON='[\"销售技能\",\"客户经营\"]' QUERY='客户说预算不够怎么办？' $0"
```

- [ ] **Step 4: Run shell syntax and deployment tests**

Run:

```bash
sh -n scripts/test_mcp.sh
uv run pytest tests/test_docker_deployment.py -v
```

Expected: shell syntax check exits 0 and all deployment tests PASS.

- [ ] **Step 5: Rewrite caller-facing documentation to the new contract**

Update all listed docs and `.env.mcp.example` so they consistently state:

```text
MCP_TOOL_PROFILE=kb 只暴露 kb_search_knowledge。
kb_search_knowledge(query, primaryDomains, knowledgeTypes=None,
retrievalMode="HYBRID", hybridWeights=None, topK=10, includeDetails=false)
```

Delete the `kb_list_knowledge_bases` and `10003` sections. Document the seven allowed labels, wrapped `10004` validation, `primaryDomain`/`domainTags` response fields, no MCP-side model classification, and no `source_kind` filtering. Include this server-Agent instruction verbatim:

```text
调用 kb_search_knowledge 前，必须根据用户问题从产品知识、合规与风控、销售技能、客户经营、行业与公司、个人成长、组织发展中选择 1 至 3 个最相关领域，并通过 primaryDomains 传入。跨领域问题可以多选；无法可靠判断时传入全部七类。不得创造体系外分类，不得省略 primaryDomains。
```

Include a concrete call:

```json
{
  "name": "kb_search_knowledge",
  "arguments": {
    "query": "客户担心保费太高怎么沟通",
    "primaryDomains": ["销售技能", "客户经营"],
    "knowledgeTypes": ["SLICE"],
    "topK": 10
  }
}
```

In deployment instructions, show the real smoke command:

```bash
PRIMARY_DOMAINS_JSON='["销售技能","客户经营"]' \
  scripts/test_mcp.sh "客户说预算不够怎么办？"
```

- [ ] **Step 6: Scan for stale public-contract references**

Run:

```bash
rg -n "kb_list_knowledge_bases|kbId|KB_ID|10003|四字段精简" \
  src/xhbx_rag/mcp_server.py tests/test_mcp_server.py scripts/test_mcp.sh \
  README.md .env.mcp.example 'docs/知识库 MCP Tool 文档.md' \
  'docs/私有化部署资源文档.md'
```

Expected: no matches. If a historical or legacy-only mention is genuinely required, rewrite it to make the legacy boundary explicit; do not leave any new-contract example using `kbId`.

- [ ] **Step 7: Run the full verification suite**

Run:

```bash
uv run pytest tests/test_mcp_server.py tests/test_docker_deployment.py -v
uv run pytest -q
```

Expected: both focused files and the full repository test suite PASS.

- [ ] **Step 8: Commit documentation and smoke-test updates**

```bash
git add scripts/test_mcp.sh tests/test_docker_deployment.py .env.mcp.example README.md
git add -f 'docs/知识库 MCP Tool 文档.md' 'docs/私有化部署资源文档.md' \
  docs/superpowers/plans/2026-07-16-mcp-primary-domain-retrieval.md
git commit -m "docs: document MCP primary domain contract"
```
