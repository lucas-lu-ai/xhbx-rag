# MCP Empty Primary Domains Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保持 `primaryDomains` 必传，同时让空数组表示不施加领域过滤并查询统一 collection 的所有文档。

**Architecture:** MCP 在参数边界规范化领域数组：非空数组转换为 `primary_domains` 硬过滤，空数组转换为 `filters={}`。服务器问答智能体负责决定“一个或多个合法领域”或“无法匹配体系时空数组”，Milvus 检索链和返回结构不变。

**Tech Stack:** Python 3.12、FastMCP、Pydantic、Milvus/PyMilvus、pytest、POSIX shell、Markdown

## Global Constraints

- `primaryDomains` 继续列在 `kb_search_knowledge` 的 required 参数中，不能省略。
- `primaryDomains=[]` 表示真正全库检索，包含 `primary_domain` 缺失、为空或异常的历史数据。
- 非空数组只允许 `产品知识`、`合规与风控`、`销售技能`、`客户经营`、`行业与公司`、`个人成长`、`组织发展`。
- MCP 不调用 chat/completions，不调用 `infer_query_domains`，不按 `source_kind` 过滤。
- 不修改 Milvus schema，不重新切片、不重新生成 embedding、不重新入库。
- 保留用户当前未提交的“一个或多个领域”意图，并统一修正代码、测试和文档措辞。

---

### Task 1: Accept Empty Required Domains as an Explicit Full-Collection Search

**Files:**
- Modify: `src/xhbx_rag/mcp_server.py:27-90,257-310,424-445`
- Modify: `tests/test_mcp_server.py:150-180,555-665`

**Interfaces:**
- Consumes: `primaryDomains: PrimaryDomainsInput` and `CANONICAL_DOMAINS: tuple[str, ...]`.
- Produces: `_normalize_primary_domains(value: Any) -> list[str]`, returning `[]` for an explicit empty array; `kb_search_knowledge(query: str, primaryDomains: PrimaryDomainsInput, knowledgeTypes: list[str] | None = None, retrievalMode: str = "HYBRID", hybridWeights: dict[str, Any] | None = None, topK: int = 10, includeDetails: bool = False) -> dict[str, Any]` passes `{}` to `EvidenceSearcher.search` when the normalized list is empty.

- [ ] **Step 1: Write failing schema, full-search, validation, and description tests**

Update the schema test to retain requiredness while allowing zero items:

```python
assert properties["primaryDomains"]["minItems"] == 0
assert properties["primaryDomains"]["maxItems"] == 7
assert search_tool.inputSchema["required"] == ["query", "primaryDomains"]
assert "一个或多个最相关领域" in search_tool.description
assert "无法匹配现有体系时传入空数组" in search_tool.description
```

Add the full-search behavior test:

```python
def test_kb_search_knowledge_empty_primary_domains_searches_all_documents():
    searcher = FakeSearcher()
    server = create_mcp_server(searcher=searcher)

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {"query": "无法匹配现有体系的问题", "primaryDomains": []},
    )

    assert payload["success"] is True
    assert searcher.calls == [{
        "query": "无法匹配现有体系的问题",
        "top_n": 20,
        "top_k": 10,
        "filters": {},
    }]
```

Remove `[]` from the invalid-input parameterization and add an over-limit case:

```python
@pytest.mark.parametrize(
    "primary_domains",
    [
        "销售技能",
        ["不存在的领域"],
        ["销售技能", 1],
        ["销售技能"] * 8,
    ],
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
        "errorMessage": (
            "参数错误: primaryDomains 必须是由 0 到 7 个合法一级领域组成的数组"
        ),
    }
    assert searcher.calls == []
```

Change the expected wrapped error to:

```python
"errorMessage": "参数错误: primaryDomains 必须是由 0 到 7 个合法一级领域组成的数组"
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
uv run pytest tests/test_mcp_server.py -k 'exposes_documented_parameters or empty_primary_domains or wraps_invalid_primary_domains' -v
```

Expected: schema test fails on `minItems=1`; empty-array call returns `10004`; prompt assertions expose the current inconsistent wording.

- [ ] **Step 3: Implement the minimal empty-array contract**

Update the schema metadata and error message:

```python
PRIMARY_DOMAINS_ERROR = (
    "参数错误: primaryDomains 必须是由 0 到 7 个合法一级领域组成的数组"
)

json_schema_extra={
    "type": "array",
    "items": {"type": "string", "enum": list(CANONICAL_DOMAINS)},
    "minItems": 0,
    "maxItems": len(CANONICAL_DOMAINS),
}
```

Allow an explicit empty list while retaining every other validation rule:

```python
def _normalize_primary_domains(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > len(CANONICAL_DOMAINS):
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
    return normalized
```

Build filters explicitly at the MCP boundary:

```python
filters = {"primary_domains": primary_domains} if primary_domains else {}
```

Use this exact server guidance in `KB_SERVER_INSTRUCTIONS`, `BOTH_SERVER_INSTRUCTIONS`, and the `kb_search_knowledge` tool description:

```text
能够匹配现有一级体系时传入一个或多个最相关领域；无法匹配现有体系时传入空数组，由 MCP 查询全部文档。
```

- [ ] **Step 4: Run the MCP test file and verify GREEN**

Run:

```bash
uv run pytest tests/test_mcp_server.py -v
```

Expected: all MCP tests PASS, including required schema, empty full-search, nonempty filtering, invalid inputs, structured content, error wrapping, and legacy profile behavior.

- [ ] **Step 5: Commit the runtime contract**

```bash
git add src/xhbx_rag/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: support empty-domain MCP full search"
```

---

### Task 2: Align Smoke Scripts and Caller Documentation

**Files:**
- Modify: `scripts/test_mcp.sh:150-160`
- Modify: `scripts/package_mcp_offline.sh:140-175`
- Modify: `tests/test_docker_deployment.py:194-212`
- Modify: `.env.mcp.example:29-35`
- Modify: `README.md:345-392`
- Modify: `docs/知识库 MCP Tool 文档.md:17-182`
- Modify: `docs/私有化部署资源文档.md:140-170,402-410`

**Interfaces:**
- Consumes: `PRIMARY_DOMAINS_JSON`, always injected as the `primaryDomains` JSON value.
- Produces: documented and smoke-tested `PRIMARY_DOMAINS_JSON='[]'` full-search calls.

- [ ] **Step 1: Write a failing smoke-script documentation assertion**

Extend `test_mcp_test_script_covers_primary_domain_search`:

```python
assert "PRIMARY_DOMAINS_JSON='[]'" in script
assert "无法匹配现有体系" in script
```

- [ ] **Step 2: Run the deployment-script test and verify RED**

Run:

```bash
uv run pytest tests/test_docker_deployment.py::test_mcp_test_script_covers_primary_domain_search -v
```

Expected: FAIL because the usage output currently only shows a nonempty domain example.

- [ ] **Step 3: Add the full-search smoke example**

Keep the existing default `PRIMARY_DOMAINS_JSON='["销售技能"]'`, then add this no-query usage line to `scripts/test_mcp.sh`:

```sh
echo "全库检索测试：PRIMARY_DOMAINS_JSON='[]' QUERY='无法匹配现有体系的问题' $0"
```

Update the generated offline README template in `scripts/package_mcp_offline.sh` with:

```bash
PRIMARY_DOMAINS_JSON='[]' \
  scripts/test_mcp.sh "无法匹配现有体系的问题"
```

- [ ] **Step 4: Update all public contract documents**

Use the following rules consistently in `.env.mcp.example`, `README.md`, `docs/知识库 MCP Tool 文档.md`, and `docs/私有化部署资源文档.md`:

```text
primaryDomains 是必传数组。能够匹配现有一级体系时传入一个或多个合法领域；无法匹配现有体系时传入空数组 []，MCP 不生成领域过滤并查询全部文档，包括未标注或异常领域的历史数据。不得省略 primaryDomains。
```

Change schema/validation documentation from 1–7 to 0–7, replace “无法判断传全部七类” with the empty-array rule, update the `10004` example, and preserve response-shape documentation.

- [ ] **Step 5: Run syntax, stale-contract, and focused tests**

Run:

```bash
sh -n scripts/test_mcp.sh
sh -n scripts/package_mcp_offline.sh
rg -n "1 至 3|无法可靠判断时传入全部七类|必须包含 1 到 7|空数组.*参数错误" \
  src/xhbx_rag/mcp_server.py README.md .env.mcp.example \
  scripts/test_mcp.sh scripts/package_mcp_offline.sh \
  'docs/知识库 MCP Tool 文档.md' 'docs/私有化部署资源文档.md'
uv run pytest tests/test_mcp_server.py tests/test_docker_deployment.py -v
```

Expected: both shell syntax checks exit 0; stale-contract scan returns no matches; all focused tests PASS.

- [ ] **Step 6: Commit scripts and documentation**

```bash
git add .env.mcp.example README.md scripts/test_mcp.sh \
  scripts/package_mcp_offline.sh tests/test_docker_deployment.py
git add -f 'docs/知识库 MCP Tool 文档.md' 'docs/私有化部署资源文档.md' \
  docs/superpowers/plans/2026-07-16-mcp-empty-primary-domains.md
git commit -m "docs: document empty-domain full search"
```

---

### Task 3: Verify and Merge the Feature Branch

**Files:**
- Verify only: repository test suite and Git state

**Interfaces:**
- Consumes: committed feature branch `codex/unified-knowledge-index`.
- Produces: merged local `main` with no uncommitted tracked changes.

- [ ] **Step 1: Run the complete verification suite**

Run:

```bash
git diff --check
uv run pytest -q
```

Expected: `git diff --check` exits 0 and the complete repository suite reports zero failures.

- [ ] **Step 2: Probe the live MCP contract in-process**

Create an injected fake searcher and run this complete in-process probe:

```bash
uv run python - <<'PY'
import asyncio

from xhbx_rag.mcp_server import create_mcp_server


class ProbeSearcher:
    def __init__(self):
        self.calls = []

    def search(self, *, query, top_n, top_k, filters=None):
        self.calls.append(filters or {})
        return {"results": []}


searcher = ProbeSearcher()
server = create_mcp_server(searcher=searcher)
tools = asyncio.run(server.list_tools())
search_tool = tools[0]
assert [tool.name for tool in tools] == ["kb_search_knowledge"]
assert search_tool.inputSchema["required"] == ["query", "primaryDomains"]
assert search_tool.inputSchema["properties"]["primaryDomains"]["minItems"] == 0
asyncio.run(server.call_tool(
    "kb_search_knowledge",
    {"query": "全库问题", "primaryDomains": []},
))
asyncio.run(server.call_tool(
    "kb_search_knowledge",
    {"query": "销售问题", "primaryDomains": ["销售技能"]},
))
assert searcher.calls == [{}, {"primary_domains": ["销售技能"]}]
PY
```

Expected: exit code 0.

- [ ] **Step 3: Merge to local main**

```bash
git status --short --untracked-files=no
git checkout main
git pull --ff-only
git merge --no-ff codex/unified-knowledge-index
```

Expected: tracked worktree is clean before checkout; `main` updates without conflict; merge completes successfully.

- [ ] **Step 4: Verify the merged result**

Run:

```bash
uv run pytest tests/test_mcp_server.py tests/test_docker_deployment.py -q
git status --short --untracked-files=no
```

Expected: focused merged tests report zero failures and tracked worktree is clean.

- [ ] **Step 5: Delete the merged feature branch**

```bash
git branch -d codex/unified-knowledge-index
```

Expected: Git confirms deletion because the branch is fully merged into `main`.
