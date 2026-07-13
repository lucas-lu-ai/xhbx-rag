# Model-Routed Collections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 移除 Web 右上角索引状态和手动 Collection 选择，让查询理解模型自动选择案例库、课程库或两者后执行检索。

**Architecture:** 查询理解结果增加稳定的 `case` / `course` 语义目标。Web 服务先执行一次查询理解，将目标映射为环境配置中的实际 Collection 名称，创建对应检索视图，再把预计算理解结果传入现有检索流水线，避免重复调用模型；显式 API `collections` 继续作为兼容性覆盖。

**Tech Stack:** Python 3.12、Pydantic、pytest、React 19、TypeScript、Vitest、Testing Library、CSS。

---

## 文件职责

- `src/xhbx_rag/query_understanding.py`：定义模型输出的 Collection 语义目标、归一化和提示词规则。
- `src/xhbx_rag/search.py`：允许检索流水线接收已经生成的查询理解结果。
- `src/xhbx_rag/answer.py`：把预计算查询理解结果从问答编排层传给检索层。
- `src/xhbx_rag/web/services.py`：执行一次查询理解、映射目标 Collection，并保留显式 API 覆盖。
- `tests/test_query_understanding.py`：验证模型路由字段契约和安全回退。
- `tests/test_indexer_search.py`：验证预计算查询理解不会再次调用模型。
- `tests/test_web_services.py`：验证单库、双库、显式覆盖和资源关闭行为。
- `web/src/App.tsx`：移除状态卡片、选择器状态和请求参数连接。
- `web/src/components/ChatView.tsx`：聊天提交不再接受或发送 Collection 列表。
- `web/src/App.chat.test.tsx`：验证页面和请求的新行为。
- `web/src/styles.css`：移除选择器样式，让引用明细占满右栏。

### Task 1: 扩展查询理解的 Collection 路由契约

**Files:**
- Modify: `tests/test_query_understanding.py`
- Modify: `src/xhbx_rag/query_understanding.py`

- [ ] **Step 1: 写入失败测试，覆盖有效目标和无效值回退**

```python
@pytest.mark.parametrize(
    ("raw_targets", "expected"),
    [
        (["case"], ["case"]),
        (["course"], ["course"]),
        (["course", "case", "course"], ["course", "case"]),
        (["unknown", ""], ["case", "course"]),
        (None, ["case", "course"]),
    ],
)
def test_query_understanding_normalizes_collection_targets(raw_targets, expected) -> None:
    payload = {
        "intent": "general_sales_qa",
        "rewritten_query": "如何讲解课程并结合实战案例？",
        "needs_retrieval": True,
        "filters": {},
    }
    if raw_targets is not None:
        payload["collection_targets"] = raw_targets

    result = QueryUnderstanding.model_validate(payload)

    assert result.collection_targets == expected


def test_query_understanding_prompt_defines_collection_routing() -> None:
    from xhbx_rag.query_understanding import _SYSTEM_PROMPT

    assert "collection_targets" in _SYSTEM_PROMPT
    assert "case" in _SYSTEM_PROMPT
    assert "course" in _SYSTEM_PROMPT
```

- [ ] **Step 2: 运行测试并确认按预期失败**

Run: `uv run pytest tests/test_query_understanding.py -q`

Expected: FAIL，提示 `QueryUnderstanding` 没有 `collection_targets`，且提示词缺少该字段。

- [ ] **Step 3: 增加字段、归一化验证器和模型提示词**

```python
CollectionTarget = Literal["case", "course"]
_DEFAULT_COLLECTION_TARGETS: list[CollectionTarget] = ["case", "course"]


class QueryUnderstanding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Intent
    rewritten_query: str = ""
    needs_retrieval: bool = True
    collection_targets: list[CollectionTarget] = Field(
        default_factory=lambda: list(_DEFAULT_COLLECTION_TARGETS)
    )
    filters: QueryFilters = Field(default_factory=QueryFilters)

    @field_validator("collection_targets", mode="before")
    @classmethod
    def _collection_targets(cls, value: object) -> list[CollectionTarget]:
        targets: list[CollectionTarget] = []
        for item in _str_list(value):
            if item in _DEFAULT_COLLECTION_TARGETS and item not in targets:
                targets.append(item)  # type: ignore[arg-type]
        return targets or list(_DEFAULT_COLLECTION_TARGETS)
```

在 `_SYSTEM_PROMPT` 字段列表中加入 `collection_targets: case | course 数组`，并明确案例问题选 `case`、课程问题选 `course`、混合或不确定问题选两者。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_query_understanding.py -q`

Expected: PASS。

- [ ] **Step 5: 提交查询理解契约**

```bash
git add tests/test_query_understanding.py src/xhbx_rag/query_understanding.py
git commit -m "feat: add model collection routing targets"
```

### Task 2: 复用查询理解结果并按模型目标创建检索视图

**Files:**
- Modify: `tests/test_indexer_search.py`
- Modify: `tests/test_web_services.py`
- Modify: `src/xhbx_rag/search.py`
- Modify: `src/xhbx_rag/answer.py`
- Modify: `src/xhbx_rag/web/services.py`

- [ ] **Step 1: 写入失败测试，证明预计算结果不会触发第二次模型调用**

```python
class _UnexpectedQueryAgent:
    def understand(self, query: str) -> QueryUnderstanding:
        raise AssertionError("不应重复执行查询理解")


def test_search_evidence_uses_precomputed_understanding() -> None:
    understanding = QueryUnderstanding(
        intent="script_search",
        rewritten_query="客户抗拒谈保险时如何开场",
        needs_retrieval=True,
        collection_targets=["case"],
        filters=QueryFilters(chunk_types=["script"], stage="售前"),
    )
    store = _FakeStore()
    store.hits = []

    result = search_evidence(
        query="客户不想聊保险怎么开场？",
        query_agent=_UnexpectedQueryAgent(),
        understanding=understanding,
        embedding_client=_FakeEmbedding(),
        store=store,
        reranker=_EmptyReranker(),
        top_n=20,
        top_k=5,
    )

    assert result["rewritten_query"] == "客户抗拒谈保险时如何开场"
```

- [ ] **Step 2: 写入服务层失败测试，覆盖案例、课程、双库和显式覆盖**

```python
@pytest.mark.parametrize(
    ("targets", "expected_names"),
    [
        (["case"], ["xhbx_sales_chunks"]),
        (["course"], ["xhbx_course_chunks"]),
        (["case", "course"], ["xhbx_sales_chunks", "xhbx_course_chunks"]),
    ],
)
def test_answer_question_routes_model_targets_to_collections(
    monkeypatch, targets, expected_names
) -> None:
    understanding = SimpleNamespace(collection_targets=targets)
    query_agent = SimpleNamespace(understand=lambda query: understanding)
    calls = {}

    monkeypatch.setattr(services.RetrievalConfig, "from_env", _fake_config)
    monkeypatch.setattr(services, "QueryUnderstandingAgent", lambda **kwargs: query_agent)
    monkeypatch.setattr(services, "EmbeddingClient", lambda **kwargs: "embedding")
    def store_factory(config, collection_names=None):
        calls["collection_names"] = collection_names
        return "store"

    monkeypatch.setattr(services, "create_retrieval_store", store_factory)
    monkeypatch.setattr(services, "RerankClient", lambda **kwargs: "reranker")
    monkeypatch.setattr(services, "AnswerAgent", lambda **kwargs: "answer")
    def fake_answer_query(**kwargs):
        calls["answer_kwargs"] = kwargs
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

    services.answer_question(query="q", top_n=20, top_k=5)

    assert calls["collection_names"] == expected_names
    assert calls["answer_kwargs"]["understanding"] is understanding
```

另加一个显式 `collections=["custom_chunks"]` 的测试，断言 `collection_names` 使用显式值，但传给 `answer_query` 的仍是同一次模型理解结果。

- [ ] **Step 3: 运行新增测试并确认失败原因正确**

Run: `uv run pytest tests/test_indexer_search.py tests/test_web_services.py -q`

Expected: FAIL，原因是 `search_evidence` / `answer_query` 尚不接受 `understanding`，服务层仍在模型理解前创建全部 Collection 的 store。

- [ ] **Step 4: 在检索和问答编排中透传预计算理解结果**

```python
def search_evidence(
    query: str,
    query_agent: _QueryAgent,
    embedding_client: _EmbeddingClient,
    store: _Store,
    reranker: _Reranker,
    top_n: int,
    top_k: int,
    trace: TraceSink | None = None,
    understanding: QueryUnderstanding | None = None,
) -> dict:
    resolved_understanding = understanding or query_agent.understand(query)
```

后续逻辑全部使用 `resolved_understanding`。`answer_query` 同样新增 `understanding: QueryUnderstanding | None = None`，并原样传入 `search_evidence`。

- [ ] **Step 5: 在 Web 服务中实现一次理解和目标映射**

```python
from ..query_understanding import (
    CollectionTarget,
    QueryUnderstanding,
    QueryUnderstandingAgent,
)


def _collection_names_for_targets(
    config: RetrievalConfig,
    targets: Sequence[CollectionTarget],
) -> list[str]:
    mapping = {
        "case": config.milvus_collection,
        "course": config.milvus_course_collection,
    }
    selected = [mapping[target] for target in targets if mapping.get(target)]
    return list(dict.fromkeys(selected)) or configured_collection_names(config)
```

在 `_answer_question_with_config` 中创建 `query_agent` 后立即调用一次 `understand(query)`；使用显式 `collections` 或 `_collection_names_for_targets` 的结果调用 `create_retrieval_store(config, collection_names=...)`，最后把 `understanding` 传给 `answer_query`。

同步更新 `tests/test_web_services.py` 的共享 stub：伪 Query Agent 需要提供 `understand()`，伪 store factory 接受 `collection_names=None`，资源关闭测试中的伪查询组件返回一个带 `collection_targets` 的理解结果。

- [ ] **Step 6: 运行相关测试确认通过**

Run: `uv run pytest tests/test_query_understanding.py tests/test_indexer_search.py tests/test_web_services.py -q`

Expected: PASS。

- [ ] **Step 7: 提交服务端自动路由**

```bash
git add tests/test_indexer_search.py tests/test_web_services.py src/xhbx_rag/search.py src/xhbx_rag/answer.py src/xhbx_rag/web/services.py
git commit -m "feat: route retrieval collections with query model"
```

### Task 3: 移除 Web 索引状态与手动 Collection 选择

**Files:**
- Modify: `web/src/App.chat.test.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/ChatView.tsx`
- Modify: `web/src/styles.css`

- [ ] **Step 1: 将旧下拉提交测试改为失败的自动路由 UI 测试**

```tsx
test("hides index status and leaves collection routing to the model", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub();
  render(<App />);

  await screen.findByLabelText("输入问题");
  expect(screen.queryByRole("heading", { name: "索引状态" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "选择 Collection" })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "引用明细" })).toBeInTheDocument();

  await user.type(screen.getByLabelText("输入问题"), "促成课程怎么讲？");
  await user.click(screen.getByRole("button", { name: "发送" }));

  await waitFor(() => {
    expect(requests).toContainEqual(
      expect.objectContaining({
        url: "/api/answer/stream",
        body: { query: "促成课程怎么讲？", top_n: 20, top_k: 5 }
      })
    );
  });
});
```

- [ ] **Step 2: 运行测试并确认旧页面行为使其失败**

Run: `cd web && npm test -- src/App.chat.test.tsx`

Expected: FAIL，因为页面仍渲染“索引状态”和“选择 Collection”。

- [ ] **Step 3: 删除前端手动选择链路**

在 `App.tsx` 中：

- 从 Lucide import 删除 `ChevronDown`、`Database`；
- 删除 Collection 显示常量、`collectionSelection`、`collectionMenuOpen`；
- 删除 `collectionOptions`、`selectedCollectionNames`、`requestCollections` 和归一化 effect；
- 删除 `toggleCollection`、`collectionNamesFromStatus`、`collectionDisplayName`、`uniqueNonEmptyStrings`；
- 调用 `ChatView` 时删除 `selectedCollections`；
- 从右侧 `<aside>` 删除整个 `<section className="status-card">`，保留 `<section className="source-detail">`。

在 `ChatView.tsx` 中删除 `selectedCollections` prop，并把请求体固定为：

```tsx
{
  query: trimmed,
  top_n: topN,
  top_k: topK
}
```

- [ ] **Step 4: 清理状态卡片和 Collection 选择器 CSS**

删除 `.status-card`、`.collection-*`、`.ok-text` 规则；将公共 padding 拆为：

```css
.source-detail {
  display: grid;
  flex: 1;
  gap: 12px;
  padding: 18px;
}
```

保留全局 `dl` / `dt` / `dd`，因为其他页面仍可能复用；不做无关样式重构。

- [ ] **Step 5: 运行前端定向测试确认通过**

Run: `cd web && npm test -- src/App.chat.test.tsx`

Expected: PASS。

- [ ] **Step 6: 提交 Web 页面调整**

```bash
git add web/src/App.chat.test.tsx web/src/App.tsx web/src/components/ChatView.tsx web/src/styles.css
git commit -m "feat: remove manual collection status controls"
```

### Task 4: 回归验证与交付

**Files:**
- Verify only: `src/`, `tests/`, `web/src/`

- [ ] **Step 1: 运行服务端完整测试**

Run: `uv run pytest -q`

Expected: 全部 PASS，无新增 warning 或错误。

- [ ] **Step 2: 运行前端完整测试**

Run: `cd web && npm test`

Expected: 全部测试 PASS。

- [ ] **Step 3: 运行前端生产构建**

Run: `cd web && npm run build`

Expected: TypeScript 编译和 Vite 构建成功；允许现有的大 chunk 体积提示，不允许新增编译错误。

- [ ] **Step 4: 检查变更卫生和残留文案**

Run: `git diff --check`

Expected: 无输出。

Run: `rg -n "索引状态|选择 Collection|selectedCollections|collection-select" web/src`

Expected: 无输出。

- [ ] **Step 5: 检查工作区和提交历史**

Run: `git status --short`

Expected: 无输出，当前分支仍为 `codex/hide-ingestion-nav`。
