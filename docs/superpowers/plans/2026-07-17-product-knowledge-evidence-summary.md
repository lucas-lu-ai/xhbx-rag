# 产品知识引用摘要展示实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `knowledge_entry` 引用在右侧顶部内容区显示“摘要、关键要点”，同时保持异议处理知识的现有三字段展示。

**Architecture:** 先扩展统一的 `evidenceText` 解析器，使产品知识字段进入现有结构化 segment 模型；再由 `EvidenceDetail` 根据 `chunk_type` 选择产品知识摘要组件或现有异议组件。后端响应和索引数据保持不变。

**Tech Stack:** React 19、TypeScript、Vitest、Testing Library

---

## 文件结构

- 修改 `web/src/evidenceText.ts`：识别产品知识的“摘要”和“关键要点”字段。
- 修改 `web/src/evidenceText.test.ts`：锁定单值摘要和 bullet 关键要点的 segment 结构。
- 修改 `web/src/components/EvidenceDetail.tsx`：新增产品知识摘要渲染器并按 `chunk_type` 分流。
- 修改 `web/src/components/EvidenceDetail.test.tsx`：覆盖产品知识正常展示、空状态和异议展示不回归。
- 不修改后端、索引、类型定义和样式文件。

### Task 1：扩展产品知识正文解析

**Files:**
- Modify: `web/src/evidenceText.ts:15-44`
- Test: `web/src/evidenceText.test.ts`

- [ ] **Step 1：编写产品知识解析失败测试**

在 `web/src/evidenceText.test.ts` 增加：

```ts
test("解析产品知识摘要和关键要点", () => {
  const segments = parseEvidenceText(
    [
      "案例：多倍保障重大疾病保险",
      "知识类型：知识条目",
      "摘要：概括产品的多次赔付和保费豁免特点。",
      "关键要点：",
      "- 六组重疾最高可赔付六次",
      "- 首次理赔后仍享有其余保障",
      "标签：",
      "- 多次赔付"
    ].join("\n")
  );

  expect(segments).toContainEqual({
    kind: "field",
    label: "摘要",
    value: "概括产品的多次赔付和保费豁免特点。",
    origin: "generated"
  });
  expect(segments).toContainEqual({
    kind: "block",
    label: "关键要点",
    items: [
      "六组重疾最高可赔付六次",
      "首次理赔后仍享有其余保障"
    ],
    origin: "generated"
  });
});
```

- [ ] **Step 2：运行测试并确认按预期失败**

Run:

```bash
cd web
npm test -- --run src/evidenceText.test.ts
```

Expected: 新用例失败；当前解析器把“摘要”和“关键要点”当作 plain 文本，无法产生目标 field/block。

- [ ] **Step 3：最小扩展结构化字段白名单**

在 `GENERATED_FIELD_LABELS` 中加入：

```ts
  "摘要",
  "关键要点",
```

不加入标题、分类或其他未用于本次展示的字段。

- [ ] **Step 4：运行解析测试并确认通过**

Run:

```bash
cd web
npm test -- --run src/evidenceText.test.ts
```

Expected: `evidenceText.test.ts` 全部通过。

- [ ] **Step 5：提交解析改动**

```bash
git add web/src/evidenceText.ts web/src/evidenceText.test.ts
git commit -m "feat: parse product knowledge summaries"
```

### Task 2：按知识类型渲染产品摘要

**Files:**
- Modify: `web/src/components/EvidenceDetail.tsx:29-66,287`
- Test: `web/src/components/EvidenceDetail.test.tsx:862-899`

- [ ] **Step 1：编写产品知识展示失败测试**

在现有正文展示测试附近增加：

```tsx
test("产品知识正文只展示摘要和关键要点", () => {
  const productKnowledge: RetrievalEvidence = {
    ...evidence,
    chunk_type: "knowledge_entry",
    text: [
      "案例：多倍保障重大疾病保险",
      "知识类型：知识条目",
      "标题：产品四大特色",
      "分类：产品知识",
      "摘要：概括产品的多次赔付和保费豁免特点。",
      "关键要点：",
      "- 六组重疾最高可赔付六次",
      "- 首次理赔后仍享有其余保障",
      "标签：",
      "- 多次赔付",
      "来源原文：",
      "- 产品课件.pptx：六组重疾最高六次赔付"
    ].join("\n")
  };

  render(<EvidenceDetail evidence={productKnowledge} index={0} />);

  const detail = screen.getByRole("article", { name: "引用1明细" });
  const labels = Array.from(
    detail.querySelectorAll(".evidence-field-label")
  ).map((node) => node.textContent);
  expect(labels).toEqual(["摘要", "关键要点"]);
  expect(
    screen.getByText("概括产品的多次赔付和保费豁免特点。")
  ).toBeInTheDocument();
  expect(screen.getByText("六组重疾最高可赔付六次")).toBeInTheDocument();
  expect(screen.getByText("首次理赔后仍享有其余保障")).toBeInTheDocument();
  expect(screen.queryByText("产品四大特色")).not.toBeInTheDocument();
  expect(screen.queryByText("产品知识")).not.toBeInTheDocument();
  expect(screen.queryByText("多次赔付")).not.toBeInTheDocument();
  expect(screen.queryByText("暂无异议处理内容。"))
    .not.toBeInTheDocument();
});
```

- [ ] **Step 2：编写产品知识专属空状态失败测试**

```tsx
test("产品知识缺少摘要和关键要点时显示专属空状态", () => {
  render(
    <EvidenceDetail
      evidence={{
        ...evidence,
        chunk_type: "knowledge_entry",
        text: "案例：多倍保障重大疾病保险\n知识类型：知识条目"
      }}
      index={0}
    />
  );

  expect(screen.getByText("暂无知识摘要。")).toBeInTheDocument();
  expect(screen.queryByText("暂无异议处理内容。"))
    .not.toBeInTheDocument();
});
```

- [ ] **Step 3：运行组件测试并确认按预期失败**

Run:

```bash
cd web
npm test -- --run src/components/EvidenceDetail.test.tsx
```

Expected: 两个产品知识用例失败；当前组件仍调用异议摘要组件。

- [ ] **Step 4：新增产品知识摘要组件**

在 `EvidenceObjectionText` 后增加：

```tsx
function EvidenceKnowledgeText({
  segments
}: {
  segments: EvidenceTextSegment[];
}) {
  const summary = segments.find(
    (segment) => segment.kind === "field" && segment.label === "摘要"
  );
  const keyPoints = segments.flatMap((segment) => {
    if (segment.kind === "plain" || segment.label !== "关键要点") {
      return [];
    }
    if (segment.kind === "block") {
      return segment.items;
    }
    return segment.kind === "field" ? [segment.value] : [];
  });

  if (!summary && keyPoints.length === 0) {
    return <p className="evidence-text">暂无知识摘要。</p>;
  }

  return (
    <div className="evidence-text evidence-struct">
      {summary?.kind === "field" && (
        <p className="evidence-struct-row">
          <span className="evidence-field-label">摘要</span>
          <span className="evidence-field-value">{summary.value}</span>
        </p>
      )}
      {keyPoints.length > 0 && (
        <div className="evidence-struct-block">
          <span className="evidence-field-label">关键要点</span>
          <ul>
            {keyPoints.map((item, index) => (
              <li key={`key-point-${index}`}>{item}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 5：按 `chunk_type` 选择内容组件**

把无条件的异议组件调用替换为：

```tsx
{evidence.chunk_type === "knowledge_entry" ? (
  <EvidenceKnowledgeText segments={textSegments} />
) : (
  <EvidenceObjectionText segments={textSegments} />
)}
```

- [ ] **Step 6：运行组件测试并确认通过**

Run:

```bash
cd web
npm test -- --run src/components/EvidenceDetail.test.tsx
```

Expected: `EvidenceDetail.test.tsx` 全部通过，包括现有异议三字段和异议空状态用例。

- [ ] **Step 7：提交组件改动**

```bash
git add web/src/components/EvidenceDetail.tsx web/src/components/EvidenceDetail.test.tsx
git commit -m "fix: show product knowledge evidence summaries"
```

### Task 3：完整回归验证

**Files:**
- Verify: `web/src/**/*.test.ts`
- Verify: `web/src/**/*.test.tsx`
- Verify: `web/src/evidenceText.ts`
- Verify: `web/src/components/EvidenceDetail.tsx`

- [ ] **Step 1：运行完整 Web 测试**

Run:

```bash
cd web
npm test
```

Expected: 所有 Vitest 测试通过，无未处理异常。

- [ ] **Step 2：运行生产构建**

Run:

```bash
cd web
npm run build
```

Expected: TypeScript 检查和 Vite 构建成功；允许保留现有的大 chunk 警告。

- [ ] **Step 3：检查最终工作区和提交范围**

Run:

```bash
git diff --check
git status --short
git log --oneline main..HEAD
```

Expected: 工作区干净；分支只包含设计、计划、解析和组件修复提交。
