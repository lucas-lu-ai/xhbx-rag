# 引用明细异议字段精简 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 右侧引用明细移除全部标签，并让正文框只展示客户异议、异议诊断和推荐回应。

**Architecture:** 仅调整 `EvidenceDetail` 展示层。继续用 `parseEvidenceText` 解析检索正文，在组件内按固定白名单与顺序挑选三个字段；来源引用和反馈渲染链不变。

**Tech Stack:** React 19、TypeScript、Vitest、Testing Library

---

### Task 1: 移除全部标签展示

**Files:**
- Modify: `web/src/components/EvidenceDetail.test.tsx:51-69`
- Modify: `web/src/components/EvidenceDetail.tsx:8-22, 437-472, 563-601`

- [ ] **Step 1: 写一个会失败的标签隐藏测试**

把原“明细渲染正文、标签、合规与元信息”测试改为“明细不渲染任何标签，仍保留标题和重排分数”，保留现有 `evidence` fixture，并使用以下断言：

```tsx
test("明细不渲染任何标签，仍保留标题和重排分数", () => {
  render(<EvidenceDetail evidence={evidence} index={0} />);

  expect(screen.getByText("引用1：案例A · 需求分析")).toBeInTheDocument();
  expect(screen.getByText("重排 0.91")).toBeInTheDocument();
  expect(screen.queryByLabelText("引用1命中标签")).not.toBeInTheDocument();
  expect(screen.queryByText("客户画像/高净值客户")).not.toBeInTheDocument();
  expect(screen.queryByText("标签提权 ×1.2")).not.toBeInTheDocument();
  expect(screen.queryByText("合规注意 · 收益承诺风险")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: 运行测试并确认因标签仍存在而失败**

Run: `cd web && npm test -- src/components/EvidenceDetail.test.tsx -t "明细不渲染任何标签"`

Expected: FAIL，`引用1命中标签` 或某个标签文本仍在 DOM 中。

- [ ] **Step 3: 删除标签展示逻辑**

在 `EvidenceDetail.tsx` 中：

1. 从 `../format` 导入中删除 `evidenceComplianceRisks` 和 `formatTagBoost`。
2. 删除 `matchedTags`、`boostLabel`、`complianceRisks` 三个局部变量。
3. 删除整个 `evidence-tag-hits` JSX 区块。
4. 把组件注释改为：

```tsx
// 右侧引用明细：异议处理摘要、来源引用溯源与逐证据打标。
```

- [ ] **Step 4: 运行标签测试并确认通过**

Run: `cd web && npm test -- src/components/EvidenceDetail.test.tsx -t "明细不渲染任何标签"`

Expected: PASS。

- [ ] **Step 5: 提交标签移除**

```bash
git add web/src/components/EvidenceDetail.tsx web/src/components/EvidenceDetail.test.tsx
git commit -m "feat: hide citation detail tags"
```

### Task 2: 正文仅展示三个异议字段

**Files:**
- Modify: `web/src/components/EvidenceDetail.test.tsx:330-430`
- Modify: `web/src/components/EvidenceDetail.tsx:340-420, 467-472, 574-601`

- [ ] **Step 1: 写一个会失败的字段过滤与顺序测试**

用一个同时包含目标字段、其他结构化字段、纯文本和案例原文的 fixture 替换原“结构化正文按字段着色区分 AI 归纳与案例原文”测试：

```tsx
test("正文只按固定顺序展示三个异议字段", () => {
  const structured: RetrievalEvidence = {
    ...evidence,
    text: [
      "案例：案例A",
      "推荐回应：先承接预算，再对齐保障缺口",
      "异议诊断：预算顾虑背后是保障优先级不清晰",
      "客户异议：客户说每年不能超过80万",
      "关联话术：script_009",
      "来源原文：",
      "- 第2节.track-0.txt：客户说每年保费预算不能超过80万"
    ].join("\n")
  };
  render(<EvidenceDetail evidence={structured} index={0} />);

  const detail = screen.getByRole("article", { name: "引用1明细" });
  const labels = Array.from(
    detail.querySelectorAll(".evidence-field-label")
  ).map((node) => node.textContent);
  expect(labels).toEqual(["客户异议", "异议诊断", "推荐回应"]);
  expect(screen.queryByText("案例")).not.toBeInTheDocument();
  expect(screen.queryByText("关联话术")).not.toBeInTheDocument();
  expect(screen.queryByText("来源原文")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: 运行测试并确认因多余字段或顺序错误而失败**

Run: `cd web && npm test -- src/components/EvidenceDetail.test.tsx -t "正文只按固定顺序展示三个异议字段"`

Expected: FAIL，实际标签仍包含案例、关联话术、来源原文，且顺序来自原文。

- [ ] **Step 3: 实现最小的白名单正文组件**

在 `EvidenceDetail.tsx` 中增加并使用以下渲染逻辑：

```tsx
const OBJECTION_DETAIL_LABELS = [
  "客户异议",
  "异议诊断",
  "推荐回应"
] as const;

function EvidenceObjectionText({
  segments
}: {
  segments: EvidenceTextSegment[];
}) {
  const values = new Map(
    segments.flatMap((segment) =>
      segment.kind === "field" &&
      OBJECTION_DETAIL_LABELS.some((label) => label === segment.label)
        ? [[segment.label, segment.value] as const]
        : []
    )
  );
  const visibleLabels = OBJECTION_DETAIL_LABELS.filter((label) =>
    values.has(label)
  );

  return (
    <div className="evidence-text evidence-struct">
      {visibleLabels.map((label) => (
        <p className="evidence-struct-row" key={label}>
          <span className="evidence-field-label">{label}</span>
          <span className="evidence-field-value">{values.get(label)}</span>
        </p>
      ))}
    </div>
  );
}
```

把正文 JSX 统一替换为：

```tsx
<EvidenceObjectionText segments={textSegments} />
```

删除不再使用的 `hasStructuredFields` 导入。原有通用结构化渲染辅助函数暂不重构，以控制本次变更范围。

- [ ] **Step 4: 运行字段过滤测试并确认通过**

Run: `cd web && npm test -- src/components/EvidenceDetail.test.tsx -t "正文只按固定顺序展示三个异议字段"`

Expected: PASS。

- [ ] **Step 5: 写一个会失败的空状态测试**

```tsx
test("正文没有异议字段时显示空状态且不回退原始全文", () => {
  render(
    <EvidenceDetail
      evidence={{ ...evidence, text: "客户担心预算，可以先承接预算。" }}
      index={0}
    />
  );

  expect(screen.getByText("暂无异议处理内容。")).toBeInTheDocument();
  expect(
    screen.queryByText("客户担心预算，可以先承接预算。")
  ).not.toBeInTheDocument();
});
```

- [ ] **Step 6: 运行空状态测试并确认失败**

Run: `cd web && npm test -- src/components/EvidenceDetail.test.tsx -t "正文没有异议字段时显示空状态"`

Expected: FAIL，页面中尚不存在“暂无异议处理内容。”。

- [ ] **Step 7: 实现空状态**

在 `EvidenceObjectionText` 计算 `visibleLabels` 后、返回字段列表前增加：

```tsx
if (visibleLabels.length === 0) {
  return <p className="evidence-text">暂无异议处理内容。</p>;
}
```

- [ ] **Step 8: 运行空状态测试并确认通过**

Run: `cd web && npm test -- src/components/EvidenceDetail.test.tsx -t "正文没有异议字段时显示空状态"`

Expected: PASS，且原始全文不在 DOM 中。

- [ ] **Step 9: 更新冲突的旧正文测试**

删除或改写以下已与新规格冲突的测试：

- `来源原文默认折叠，点击标题展开、再点击折叠`
- `关联话术保持 ID 展示，点击后内联展开完整话术`
- `AI 归纳的 bullet 块默认展开`

这些能力不再属于右侧正文框；来源引用区的既有交互测试继续保留。

- [ ] **Step 10: 运行组件测试并确认通过**

Run: `cd web && npm test -- src/components/EvidenceDetail.test.tsx`

Expected: 该文件全部测试 PASS。

- [ ] **Step 11: 提交正文过滤**

```bash
git add web/src/components/EvidenceDetail.tsx web/src/components/EvidenceDetail.test.tsx
git commit -m "feat: limit citation detail to objection fields"
```

### Task 3: 回归验证

**Files:**
- Verify: `web/src/components/EvidenceDetail.tsx`
- Verify: `web/src/components/EvidenceDetail.test.tsx`

- [ ] **Step 1: 运行完整前端测试**

Run: `cd web && npm test`

Expected: 全部 Vitest 测试 PASS，无失败用例。

- [ ] **Step 2: 运行生产构建**

Run: `cd web && npm run build`

Expected: TypeScript 检查和 Vite 构建成功，退出码为 0。

- [ ] **Step 3: 检查最终差异**

Run: `git diff HEAD~2 --check && git status --short`

Expected: 无空白错误；工作区只允许出现 Vite 构建产生且已被忽略的文件。
