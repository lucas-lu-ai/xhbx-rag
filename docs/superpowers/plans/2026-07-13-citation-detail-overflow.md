# 引用详情横向溢出调整 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让右侧引用详情不再横向滚动，长来源单行省略，同时合并位置与定位并移除 Finder 按钮。

**Architecture:** 保持 `EvidenceDetail` 的引用选择和反馈状态结构不变，只收缩其展示职责：组件负责组合“位置与定位”文本并移除文件揭示副作用，样式负责约束所有详情容器的横向尺寸和长字符串呈现。使用现有 Vitest/Testing Library 覆盖可观察的 DOM 行为，再通过前端测试和生产构建验证样式与类型。

**Tech Stack:** React 18、TypeScript、CSS、Vitest、Testing Library、Vite

---

## 文件结构

- Modify: `web/src/components/EvidenceDetail.tsx` — 合并位置与定位展示，删除 Finder API、状态和按钮。
- Modify: `web/src/components/EvidenceDetail.test.tsx` — 覆盖合并字段、缺失值、按钮移除及长来源完整提示。
- Modify: `web/src/styles.css` — 禁止详情面板横向溢出，并确保来源胶囊单行省略。

### Task 1: 收缩引用详情展示行为

**Files:**
- Modify: `web/src/components/EvidenceDetail.test.tsx:106-139`
- Modify: `web/src/components/EvidenceDetail.tsx:1-130, 292-398`

- [ ] **Step 1: 写入失败测试**

删除原有“调用 reveal 接口”的测试，加入以下用例；同时从测试文件移除不再使用的 `installFetchStub` 导入：

```tsx
test("位置与定位合并展示并移除 Finder 按钮", () => {
  render(<EvidenceDetail evidence={evidence} index={0} />);

  expect(screen.getByText("位置与定位")).toBeInTheDocument();
  expect(screen.getByText("L1 · 精确定位")).toBeInTheDocument();
  expect(screen.queryByText(/^位置$/)).not.toBeInTheDocument();
  expect(screen.queryByText(/^定位$/)).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "在 Finder 中显示文件" })
  ).not.toBeInTheDocument();
});

test.each([
  ["只有位置", { display_location: "slide46", locator_confidence: undefined }, "slide46"],
  ["只有定位", { display_location: undefined, locator_confidence: "approximate" }, "近似定位"],
  ["全部缺失", { display_location: undefined, locator_confidence: undefined }, "未提供"]
])("位置与定位在%s时正确展示", (_name, citationPatch, expected) => {
  render(
    <EvidenceDetail
      evidence={{
        ...evidence,
        citations: [{ ...evidence.citations![0], ...citationPatch }]
      }}
      index={0}
    />
  );

  expect(screen.getByText(expected)).toBeInTheDocument();
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd web && npm test -- --run src/components/EvidenceDetail.test.tsx`

Expected: FAIL，页面仍分别显示“位置”“定位”，并存在 Finder 按钮。

- [ ] **Step 3: 实现最小组件调整**

在 `EvidenceDetail.tsx` 中删除 `ExternalLink`、`revealSource`、`revealMessage` 和 `handleReveal`。在 `selectedCitation` 后计算组合值：

```tsx
const locationAndConfidence = selectedCitation
  ? [
      selectedCitation.display_location,
      formatLocatorConfidence(selectedCitation.locator_confidence)
    ]
      .filter(Boolean)
      .join(" · ") || "未提供"
  : "未提供";
```

将原来的双列 `detail-grid` 和 Finder 操作替换为单个信息块：

```tsx
<div className="detail-block">
  <span>位置与定位</span>
  <strong>{locationAndConfidence}</strong>
</div>
```

保留文件和原文摘录信息块，不修改来源切换与反馈逻辑。

- [ ] **Step 4: 运行聚焦测试确认通过**

Run: `cd web && npm test -- --run src/components/EvidenceDetail.test.tsx`

Expected: `EvidenceDetail.test.tsx` 全部 PASS。

- [ ] **Step 5: 提交组件行为调整**

```bash
git add web/src/components/EvidenceDetail.tsx web/src/components/EvidenceDetail.test.tsx
git commit -m "feat: simplify citation detail source metadata"
```

### Task 2: 消除详情面板横向滚动

**Files:**
- Modify: `web/src/components/EvidenceDetail.test.tsx:53-105`
- Modify: `web/src/styles.css:1074-1083, 1235-1244, 1658-1714`

- [ ] **Step 1: 写入长来源回归测试**

加入测试确认长来源仍使用可省略的来源按钮，并通过 `title` 暴露完整信息：

```tsx
test("长来源保持单行省略入口并保留完整提示", () => {
  const longFilename = `${"很长的来源文件名".repeat(12)}.pptx`;
  render(
    <EvidenceDetail
      evidence={{
        ...evidence,
        citations: [{
          ...evidence.citations![0],
          filename: longFilename,
          source_path: `/data/${longFilename}`,
          display_location: "slide46 · L38-L42"
        }]
      }}
      index={0}
    />
  );

  const sourceButton = screen.getByRole("button", { name: new RegExp(longFilename) });
  expect(sourceButton).toHaveClass("evidence-source", "selectable");
  expect(sourceButton).toHaveAttribute("title", expect.stringContaining(longFilename));
});
```

- [ ] **Step 2: 运行测试，记录当前 DOM 保护已通过**

Run: `cd web && npm test -- --run src/components/EvidenceDetail.test.tsx`

Expected: PASS；该测试锁定实现省略所依赖的按钮类和完整 `title`，下一步只调整 CSS。

- [ ] **Step 3: 收紧横向尺寸与滚动样式**

在 `web/src/styles.css` 中加入或调整以下规则：

```css
.source-detail {
  min-width: 0;
  overflow-x: hidden;
  overflow-y: auto;
}

.evidence-detail-slot,
.evidence-detail,
.source-stack,
.evidence-source-list {
  min-width: 0;
  max-width: 100%;
}

.evidence-text {
  overflow-x: hidden;
  overflow-y: auto;
}

.evidence-source.selectable {
  display: block;
  width: 100%;
  max-width: 100%;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
```

保留 `.detail-block strong` 和 `.excerpt-box p` 的 `overflow-wrap: anywhere`，确保文件路径、位置和摘录不会撑宽容器。来源列表仍可纵向展开/收起，但每条来源独占一行并在末尾省略。

- [ ] **Step 4: 运行前端全量测试**

Run: `cd web && npm test`

Expected: 所有前端测试 PASS。

- [ ] **Step 5: 运行生产构建与差异检查**

Run: `cd web && npm run build`

Expected: TypeScript 检查与 Vite 构建成功；允许现有 chunk-size 警告。

Run: `git diff --check`

Expected: 无输出，退出码为 0。

- [ ] **Step 6: 提交溢出样式调整**

```bash
git add web/src/styles.css web/src/components/EvidenceDetail.test.tsx
git commit -m "fix: prevent horizontal citation detail overflow"
```

### Task 3: 最终回归验证

**Files:**
- Verify only

- [ ] **Step 1: 确认已删除 Finder 前端依赖**

Run: `rg -n "revealSource|在 Finder 中显示文件|revealMessage|ExternalLink" web/src/components/EvidenceDetail.tsx web/src/components/EvidenceDetail.test.tsx`

Expected: 无匹配，`rg` 退出码为 1。

- [ ] **Step 2: 再次运行前端测试与构建**

Run: `cd web && npm test && npm run build`

Expected: 所有测试 PASS，生产构建成功；允许现有 chunk-size 警告。

- [ ] **Step 3: 确认工作区状态**

Run: `git status --short && git log -3 --oneline`

Expected: 工作区无未提交改动，最近提交包含组件行为与横向溢出修复。
