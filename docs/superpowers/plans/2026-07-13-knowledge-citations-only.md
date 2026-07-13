# Knowledge Citations Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将“检索证据”改为只展示模型实际引用内容的“知识引用”，并让右侧详情以连续引用序号和知识名称作为主标题。

**Architecture:** 在 `EvidenceDetailContext` 中构造同时携带原始证据索引和可见引用序号的引用视图条目。列表和详情只消费引用视图，但 bad case、相关话术查找和反馈提交继续使用完整召回数组及原始索引，避免数据丢失或点击错位。

**Tech Stack:** React、TypeScript、Vitest、Testing Library、CSS、Vite

---

### Task 1: 引用视图与稳定索引映射

**Files:**
- Create: `web/src/components/EvidenceDetailContext.test.ts`
- Modify: `web/src/components/EvidenceDetailContext.tsx`

- [ ] **Step 1: 写入引用视图和自动选择失败测试**

创建 `web/src/components/EvidenceDetailContext.test.ts`：

```ts
import {
  citedEvidenceEntries,
  citedEvidenceIndexes,
  firstEvidenceKey
} from "./EvidenceDetailContext";
import type { Citation, RetrievalEvidence } from "../types";

const evidences: RetrievalEvidence[] = [
  { chunk_id: "c1", text: "召回一" },
  { chunk_id: "c2", text: "召回二" },
  { chunk_id: "c3", text: "召回三" }
];

const citations: Citation[] = [
  {
    selected: true,
    evidence_index: 3,
    display_location: "L3",
    display_excerpt: "引用三",
    can_reveal: false
  },
  {
    selected: false,
    evidence_index: 2,
    display_location: "L2",
    display_excerpt: "兜底二",
    can_reveal: false
  },
  {
    selected: true,
    evidence_index: 1,
    display_location: "L1",
    display_excerpt: "引用一",
    can_reveal: false
  }
];

test("引用视图按原始证据顺序生成连续显示序号并保留原始索引", () => {
  const entries = citedEvidenceEntries(
    evidences,
    citedEvidenceIndexes(citations)
  );

  expect(entries).toEqual([
    { evidence: evidences[0], evidenceIndex: 0, displayIndex: 0 },
    { evidence: evidences[2], evidenceIndex: 2, displayIndex: 1 }
  ]);
});

test("自动选择第一条实际引用而不是第一条召回证据", () => {
  expect(
    firstEvidenceKey("turn-1", citations.slice(0, 2), evidences)
  ).toBe("turn-1:evidence-2");
});

test("没有模型实际引用时不自动选择召回证据", () => {
  expect(firstEvidenceKey("turn-1", citations.slice(1, 2), evidences)).toBeNull();
  expect(firstEvidenceKey("turn-1", [], evidences)).toBeNull();
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd web && npm test -- --run src/components/EvidenceDetailContext.test.ts`

Expected: FAIL，提示尚未导出 `citedEvidenceEntries`，且旧 `firstEvidenceKey` 会在无实际引用时回退第一条召回证据。

- [ ] **Step 3: 实现引用视图条目并收紧自动选择**

在 `web/src/components/EvidenceDetailContext.tsx` 增加：

```ts
export type CitedEvidenceEntry = {
  evidence: RetrievalEvidence;
  evidenceIndex: number;
  displayIndex: number;
};

export function citedEvidenceEntries(
  evidences: RetrievalEvidence[],
  citedIndexes: Set<number>
): CitedEvidenceEntry[] {
  const entries: CitedEvidenceEntry[] = [];
  evidences.forEach((evidence, evidenceIndex) => {
    if (!citedIndexes.has(evidenceIndex + 1)) {
      return;
    }
    entries.push({
      evidence,
      evidenceIndex,
      displayIndex: entries.length
    });
  });
  return entries;
}
```

将 `firstEvidenceKey` 改为严格选择第一条实际引用：

```ts
export function firstEvidenceKey(
  keyPrefix: string,
  citations: Citation[],
  evidences: RetrievalEvidence[]
): string | null {
  const [firstCited] = citedEvidenceEntries(
    evidences,
    citedEvidenceIndexes(citations)
  );
  return firstCited
    ? evidenceKey(keyPrefix, firstCited.evidenceIndex)
    : null;
}
```

同时把旧注释改为“只自动选中第一条模型实际引用；没有实际引用时保持未选中”。

- [ ] **Step 4: 运行测试并确认通过**

Run: `cd web && npm test -- --run src/components/EvidenceDetailContext.test.ts`

Expected: PASS，3 项测试通过。

- [ ] **Step 5: 提交引用视图基础能力**

```bash
git add web/src/components/EvidenceDetailContext.tsx web/src/components/EvidenceDetailContext.test.ts
git commit -m "feat: derive model-selected knowledge citations"
```

### Task 2: 知识引用列表过滤与原始索引联动

**Files:**
- Modify: `web/src/components/EvidenceList.tsx`
- Modify: `web/src/components/EvidenceList.test.tsx`
- Modify: `web/src/components/BadCasePanel.tsx`

- [ ] **Step 1: 将列表测试改为知识引用行为**

在 `web/src/components/EvidenceList.test.tsx` 中让默认 `citedIndexes` 为 `new Set([2])`，并将展开辅助函数和用例改为：

```ts
function expandList() {
  fireEvent.click(screen.getByRole("button", { name: /知识引用/ }));
}

test("知识引用默认折叠，标题只显示实际引用条数", () => {
  renderList();

  const toggle = screen.getByRole("button", { name: /知识引用/ });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByText("1 条")).toBeInTheDocument();
  expect(screen.queryByText(/答案引用/)).not.toBeInTheDocument();
});

test("展开后只显示实际引用并使用连续可见序号", () => {
  renderList();
  expandList();

  const list = screen.getByRole("region", { name: "知识引用列表" });
  const rows = within(list).getAllByRole("button");
  expect(rows).toHaveLength(1);
  expect(within(rows[0]).getByText("1")).toBeInTheDocument();
  expect(within(rows[0]).getByText("证据 2")).toBeInTheDocument();
  expect(within(list).queryByText("案例A · 需求分析")).not.toBeInTheDocument();
  expect(within(list).queryByText("答案引用")).not.toBeInTheDocument();
});

test("点击可见引用仍回调原始证据 key", () => {
  const onSelectEvidence = vi.fn();
  renderList({ onSelectEvidence });
  expandList();

  fireEvent.click(screen.getByRole("button", { name: /证据 2/ }));
  expect(onSelectEvidence).toHaveBeenCalledWith("turn-1:evidence-1");
});

test("没有实际引用时不渲染知识引用区", () => {
  renderList({ citedIndexes: new Set() });
  expect(screen.queryByLabelText("知识引用")).not.toBeInTheDocument();
});
```

测试文件补充 `within` 导入。保留类型标签、分数和预览断言，但只针对实际引用行。

- [ ] **Step 2: 运行列表测试并确认失败**

Run: `cd web && npm test -- --run src/components/EvidenceList.test.tsx`

Expected: FAIL，旧组件仍显示“检索证据”、全部召回条目和“答案引用”徽标。

- [ ] **Step 3: 实现知识引用列表投影**

在 `EvidenceList.tsx` 导入 `citedEvidenceEntries`，组件内先生成条目并在空集合时返回 `null`：

```tsx
const citedEntries = citedEvidenceEntries(evidences, citedIndexes);
if (citedEntries.length === 0) {
  return null;
}
```

把标题和列表改为：

```tsx
<div className="evidence-section" aria-label="知识引用">
  <button
    type="button"
    className="pane-heading compact-heading evidence-toggle"
    aria-expanded={expanded}
    onClick={() => setExpanded((value) => !value)}
  >
    {expanded ? (
      <ChevronDown size={14} aria-hidden="true" />
    ) : (
      <ChevronRight size={14} aria-hidden="true" />
    )}
    <Search size={18} aria-hidden="true" />
    <strong>知识引用</strong>
    <span className="evidence-count">{citedEntries.length} 条</span>
  </button>
  {expanded && (
    <div className="evidence-list" role="region" aria-label="知识引用列表">
      {citedEntries.map(({ evidence, evidenceIndex, displayIndex }) => {
        const key = evidenceKey(keyPrefix, evidenceIndex);
        const selected = key === selectedEvidenceKey;
        const meta = formatEvidenceMeta(evidence.metadata);
        const score = formatScore(evidence.rerank_score);
        const preview = evidence.text_preview || evidence.text || "没有正文内容。";
        return (
          <button
            className={selected ? "evidence-row selected" : "evidence-row"}
            key={`${evidence.chunk_id ?? "evidence"}-${evidenceIndex}`}
            type="button"
            aria-pressed={selected}
            onClick={() => onSelectEvidence?.(key)}
          >
            <span className="evidence-row-index">{displayIndex + 1}</span>
            <span className="evidence-row-main">
              <span className="evidence-row-title">
                <strong>{meta || "未命名知识"}</strong>
                <span className="evidence-type-chip">
                  {formatChunkType(evidence.chunk_type)}
                </span>
              </span>
              <span className="evidence-row-preview">{preview}</span>
            </span>
            {score && <span className="evidence-row-score">{score}</span>}
          </button>
        );
      })}
    </div>
  )}
</div>
```

- [ ] **Step 4: 让 BadCasePanel 只为实际引用建立详情联动**

在 `BadCasePanel.tsx` 导入 `citedEvidenceEntries`，在取得 `citedIndexes` 后生成：

```ts
const citedEntries = citedEvidenceEntries(evidences, citedIndexes);
const selectedEvidenceIndex = evidenceIndexForPrefix(
  selectedEvidenceKey,
  turn.id
);
const selectedEntry =
  selectedEvidenceIndex === null
    ? undefined
    : citedEntries.find(
        (entry) => entry.evidenceIndex === selectedEvidenceIndex
      );
```

列表继续传完整 `evidences` 和 `citedIndexes`，但只在 `citedEntries.length > 0` 时渲染。右侧详情改用 `selectedEntry`：

```tsx
{selectedEntry && container &&
  createPortal(
    <EvidenceDetail
      key={selectedEvidenceKey}
      evidence={selectedEntry.evidence}
      relatedEvidences={evidences}
      index={selectedEntry.displayIndex}
      cited
      feedbackJudgement={
        evidenceFeedback[
          evidenceFeedbackKey(
            selectedEntry.evidenceIndex,
            selectedEntry.evidence
          )
        ]?.judgement
      }
      onToggleFeedback={(judgement) =>
        toggleEvidenceFeedback(
          selectedEntry.evidenceIndex,
          selectedEntry.evidence,
          judgement
        )
      }
      onSubmitUseful={() =>
        submitEvidenceUseful(
          selectedEntry.evidenceIndex,
          selectedEntry.evidence
        )
      }
      onSubmitNotUseful={(reason) =>
        submitEvidenceNotUseful(
          selectedEntry.evidenceIndex,
          selectedEntry.evidence,
          reason
        )
      }
    />,
    container
  )}
```

`submitEvidenceUseful`、`submitEvidenceNotUseful` 的 payload 继续使用完整 `evidences`，不改 API 数据。
同步把 `BadCasePanel` 的“检索证据面板”注释改为“知识引用面板”，保持代码语义与界面一致。

- [ ] **Step 5: 运行列表测试并确认通过**

Run: `cd web && npm test -- --run src/components/EvidenceList.test.tsx src/components/EvidenceDetailContext.test.ts`

Expected: PASS，引用视图和列表测试全部通过。

- [ ] **Step 6: 提交知识引用列表改动**

```bash
git add web/src/components/EvidenceList.tsx web/src/components/EvidenceList.test.tsx web/src/components/BadCasePanel.tsx
git commit -m "feat: show only model-selected knowledge citations"
```

### Task 3: 引用明细标题与单次/批量集成

**Files:**
- Modify: `web/src/components/EvidenceDetail.tsx`
- Modify: `web/src/components/EvidenceDetail.test.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.chat.test.tsx`
- Modify: `web/src/App.batch.test.tsx`
- Modify: `web/src/styles.css`

- [ ] **Step 1: 写入引用明细标题测试**

把 `EvidenceDetail.test.tsx` 首个用例改为：

```tsx
test("引用明细以连续引用序号和知识名称作为标题", () => {
  render(<EvidenceDetail evidence={evidence} index={0} />);

  expect(screen.getByRole("article", { name: "引用1明细" })).toBeInTheDocument();
  expect(screen.getByText("引用1：案例A · 需求分析")).toBeInTheDocument();
  expect(screen.queryByText("证据 1 · 异议处理")).not.toBeInTheDocument();
  expect(screen.queryByText("答案引用")).not.toBeInTheDocument();
  expect(screen.queryByText("案例A · 需求分析")).not.toBeInTheDocument();
  expect(screen.getByText("重排 0.91")).toBeInTheDocument();
});

test("引用明细缺少 metadata 时显示未命名知识", () => {
  render(
    <EvidenceDetail
      evidence={{ ...evidence, metadata: undefined }}
      index={1}
    />
  );

  expect(screen.getByText("引用2：未命名知识")).toBeInTheDocument();
});
```

把该测试文件中所有 `<EvidenceDetail ... cited />` 和 `cited={false}` 删除；反馈控件的无障碍断言由“证据 1 应该用 / 不该用”改为“引用1应该用 / 不该用”。

- [ ] **Step 2: 更新单次问答和批量集成测试期望**

在 `App.chat.test.tsx` 中把两证据用例改为只期望原始第 2 条：

```tsx
await user.click(await screen.findByRole("button", { name: /知识引用/ }));
const knowledgeList = await screen.findByRole("region", {
  name: "知识引用列表"
});
const rows = within(knowledgeList).getAllByRole("button");
expect(rows).toHaveLength(1);
expect(within(rows[0]).getByText("1")).toBeInTheDocument();
expect(within(rows[0]).getByText("案例A · 阶段2")).toBeInTheDocument();
expect(within(knowledgeList).queryByText("证据1正文内容。")).not.toBeInTheDocument();
expect(within(knowledgeList).queryByText("答案引用")).not.toBeInTheDocument();
```

把聊天详情断言更新为：

```tsx
expect(await screen.findByText("知识引用")).toBeInTheDocument();
const detailPane = screen.getByRole("complementary", { name: "索引和溯源" });
expect(within(detailPane).getByText("引用明细")).toBeInTheDocument();
expect(within(detailPane).getByText("引用1：案例A · 需求分析")).toBeInTheDocument();
```

把 `App.batch.test.tsx` 的批量详情断言更新为：

```tsx
expect(await within(detailPane).findByText("引用1：案例A · 需求分析")).toBeInTheDocument();
await user.click(screen.getByRole("button", { name: /知识引用/ }));
const knowledgeList = await screen.findByRole("region", {
  name: "知识引用列表"
});
expect(within(knowledgeList).getAllByRole("button")).toHaveLength(1);
```

并将所有旧的“检索证据 / 检索证据列表 / 证据 1 · 异议处理”可见文案断言替换为新文案。

- [ ] **Step 3: 运行明细与集成测试并确认失败**

Run: `cd web && npm test -- --run src/components/EvidenceDetail.test.tsx src/App.chat.test.tsx src/App.batch.test.tsx`

Expected: FAIL，旧详情仍显示知识类型、重复徽标和旧“证据明细”标题。

- [ ] **Step 4: 实现引用明细语义**

在 `EvidenceDetail.tsx` 中：

- 从 import 中删除 `formatChunkType`；
- 从 `EvidenceDetailProps` 删除 `cited`；
- 用 `index` 作为过滤后的可见引用序号；
- 删除 metadata 的重复 `<p className="meta-text">`。

详情顶部改为：

```tsx
const citationNumber = index + 1;
const knowledgeName = meta || "未命名知识";

return (
  <article className="evidence-detail" aria-label={`引用${citationNumber}明细`}>
    <div className="evidence-header">
      <strong>引用{citationNumber}：{knowledgeName}</strong>
      <span className="evidence-header-side">
        {score && <span>重排 {score}</span>}
      </span>
    </div>
```

把标签区和反馈区的 aria-label 统一为：

```tsx
aria-label={`引用${citationNumber}命中标签`}
aria-label={`引用${citationNumber}打标`}
aria-label={`引用${citationNumber}应该用`}
aria-label={`引用${citationNumber}不该用`}
```

删除 `styles.css` 中已无组件使用的 `.evidence-cited-badge` 和 `.evidence-row-title .evidence-cited-badge` 样式块。

- [ ] **Step 5: 更新 App 右侧标题和空状态**

在 `App.tsx` 中改为：

```tsx
<div className="pane-heading">
  <FileText size={20} aria-hidden="true" />
  <h2>引用明细</h2>
</div>
```

未选择时的提示改为：

```tsx
{hasEvidenceContext
  ? "点击一条知识引用查看明细。"
  : "暂无引用。"}
```

- [ ] **Step 6: 运行相关测试并确认通过**

Run: `cd web && npm test -- --run src/components/EvidenceDetailContext.test.ts src/components/EvidenceList.test.tsx src/components/EvidenceDetail.test.tsx src/App.chat.test.tsx src/App.batch.test.tsx`

Expected: PASS，相关单元与集成测试全部通过。

- [ ] **Step 7: 运行完整前端测试与生产构建**

Run: `cd web && npm test -- --run && npm run build`

Expected: 全部前端测试通过，TypeScript 与 Vite 生产构建成功；既有大 chunk 警告不计为失败。

- [ ] **Step 8: 检查并提交最终改动**

```bash
git diff --check
git status --short
git add web/src/components/EvidenceDetail.tsx web/src/components/EvidenceDetail.test.tsx web/src/App.tsx web/src/App.chat.test.tsx web/src/App.batch.test.tsx web/src/styles.css docs/superpowers/plans/2026-07-13-knowledge-citations-only.md
git commit -m "feat: present model citations as knowledge references"
```
