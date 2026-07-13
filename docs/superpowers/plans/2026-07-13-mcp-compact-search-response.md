# MCP Compact Search Response Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `kb_search_knowledge` 增加默认关闭的 `includeDetails` 参数，默认把每条结果精简为正文、第一条引用路径和文件名，并允许显式开启原完整结构。

**Architecture:** 检索链和外层 `McpResponse` 保持不变。在 MCP 工具入口根据 `includeDetails` 选择现有完整格式化函数或新增的精简格式化函数；精简函数只读取检索结果的 `text` 和第一条合法 citation。

**Tech Stack:** Python 3.11+、FastMCP、pytest、Pydantic 生成的工具输入 schema。

## Global Constraints

- `includeDetails: bool = False`，未传参数时返回精简响应。
- `includeDetails=false` 时，`data` 的每条结果只能包含 `content`、`source_path`、`filename`；只有显式传 `true` 才返回原完整结构。
- 多条 citation 只读取第一条；citation 或字段缺失时返回空字符串。
- 旧版 `search_knowledge`、检索、过滤、融合、重排和错误响应均保持不变。

---

### Task 1: 增加可切换的精简返回模式

**Files:**
- Modify: `tests/test_mcp_server.py:167-249`
- Modify: `src/xhbx_rag/mcp_server.py:266-308`
- Modify: `src/xhbx_rag/mcp_server.py:457-489`
- Modify: `README.md:323-330`

**Interfaces:**
- Consumes: `active_searcher.search(...) -> dict[str, Any]`，其中检索条目使用 `text` 和 `citations`。
- Produces: `kb_search_knowledge(..., includeDetails: bool = False) -> dict` 和 `_format_compact_kb_search_results(result: dict[str, Any]) -> list[dict[str, str]]`。

- [ ] **Step 1: 为工具 schema 和显式完整模式写失败测试**

在 `test_kb_search_knowledge_exposes_documented_parameters` 中加入：

```python
assert properties["includeDetails"]["default"] is False
```

把 `test_kb_search_knowledge_returns_wrapped_slice_results` 的调用参数增加 `"includeDetails": True`，验证显式开启时仍返回原结构；再新增以下聚焦断言：

```python
def test_kb_search_knowledge_returns_full_results_when_details_enabled():
    searcher = FakeSearcher(
        result={"results": [{"chunk_id": "c1", "text": "完整正文"}]}
    )
    server = create_mcp_server(searcher=searcher)

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {"query": "客户经营", "kbId": 1, "includeDetails": True},
    )

    assert payload["data"][0]["id"] == "c1"
    assert payload["data"][0]["slice"]["fullContent"] == "完整正文"
```

- [ ] **Step 2: 为精简模式及引用边界写失败测试**

在 `tests/test_mcp_server.py` 中新增：

```python
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
                    "citations": [
                        {"source_path": "案例A/a.txt", "filename": "a.txt"},
                        {"source_path": "案例B/b.txt", "filename": "b.txt"},
                    ],
                }
            ]
        }
    )
    server = create_mcp_server(searcher=searcher)

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {"query": "客户经营", "kbId": 1, **details_argument},
    )

    assert payload["data"] == [
        {
            "content": "完整正文",
            "source_path": "案例A/a.txt",
            "filename": "a.txt",
        }
    ]


@pytest.mark.parametrize(
    "citations",
    [None, [], ["不是对象"], [{}]],
)
def test_compact_kb_search_results_use_empty_source_fields_for_missing_citation(
    citations,
):
    raw = {"text": "正文"}
    if citations is not None:
        raw["citations"] = citations
    server = create_mcp_server(searcher=FakeSearcher(result={"results": [raw]}))

    payload = _call_tool(
        server,
        "kb_search_knowledge",
        {"query": "客户经营", "kbId": 1, "includeDetails": False},
    )

    assert payload["data"] == [
        {"content": "正文", "source_path": "", "filename": ""}
    ]
```

- [ ] **Step 3: 运行新增测试并确认失败原因正确**

Run:

```bash
uv run pytest tests/test_mcp_server.py -k 'includeDetails or details_enabled or compact' -v
```

Expected: FAIL，工具 schema 没有 `includeDetails`，或 FastMCP 报告该参数尚未定义。

- [ ] **Step 4: 实现参数分流和精简格式化**

在 `kb_search_knowledge` 的 `topK` 参数后加入：

```python
includeDetails: bool = False,
```

把检索成功后的现有返回语句：

```python
return _mcp_success(_format_kb_search_results(result))
```

替换为：

```python
formatted = (
    _format_kb_search_results(result)
    if includeDetails
    else _format_compact_kb_search_results(result)
)
return _mcp_success(formatted)
```

在 `_format_kb_search_results` 之前增加：

```python
def _format_compact_kb_search_results(
    result: dict[str, Any],
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    raw_results = result.get("results", [])
    if not isinstance(raw_results, list):
        return items
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        citations = raw.get("citations")
        first_citation = (
            citations[0]
            if isinstance(citations, list)
            and citations
            and isinstance(citations[0], dict)
            else {}
        )
        items.append(
            {
                "content": str(raw.get("text") or ""),
                "source_path": str(first_citation.get("source_path") or ""),
                "filename": str(first_citation.get("filename") or ""),
            }
        )
    return items
```

- [ ] **Step 5: 运行 MCP 测试并确认通过**

Run:

```bash
uv run pytest tests/test_mcp_server.py -v
```

Expected: 全部 PASS。

- [ ] **Step 6: 更新 MCP 使用文档**

在 `README.md` 的 `kb_search_knowledge` 参数说明中补充：

```markdown
`includeDetails` 默认为 `false`，每条结果只返回完整正文 `content`，以及
第一条引用的 `source_path` 和 `filename`；设为 `true` 时返回完整切片
结构。引用不存在时两个来源字段为空字符串。
```

- [ ] **Step 7: 运行回归检查**

Run:

```bash
uv run pytest tests/test_mcp_server.py tests/test_search.py -v
git diff --check
```

Expected: 测试全部 PASS，`git diff --check` 无输出且退出码为 0。

- [ ] **Step 8: 提交实现**

```bash
git add src/xhbx_rag/mcp_server.py tests/test_mcp_server.py README.md
git commit -m "feat: 支持 MCP 精简检索返回"
```
