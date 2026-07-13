# 批量详情页大模型回答 Markdown 渲染 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让批量任务详情页的大模型回答使用与聊天页相同的 Markdown 渲染。

**Architecture:** 复用现有 `MarkdownMessage` 组件替换批量详情页的纯文本 `<p>`，保持回答数据流及其余页面结构不变。使用批量页面集成测试验证生成的 Markdown 语义节点。

**Tech Stack:** React、TypeScript、react-markdown、remark-gfm、Vitest、Testing Library

---

### Task 1: 定义批量回答的 Markdown 渲染行为

**Files:**
- Modify: `web/src/App.batch.test.tsx:242-307`

- [ ] **Step 1: 让终态批量回答夹具包含 Markdown**

将 `completedDetail` 中的问题构造改为：

```tsx
questions: [
  batchRunQuestionDetail({
    status: "succeeded",
    response: {
      ...answerPayload,
      answer: "先**承接预算**：\n\n- 讨论缴费期\n- 对齐保障缺口"
    }
  })
]
```

- [ ] **Step 2: 断言回答生成加粗和列表语义**

进入详情页后加入：

```tsx
expect(await screen.findByText("承接预算")).toHaveProperty("tagName", "STRONG");
const answerListItem = screen.getByText("讨论缴费期");
expect(answerListItem).toHaveProperty("tagName", "LI");
expect(answerListItem.parentElement).toHaveProperty("tagName", "UL");
```

- [ ] **Step 3: 运行目标测试并确认 RED**

Run: `cd web && npm test -- --run src/App.batch.test.tsx -t "批量会话轮询进度直到终态并展示行状态"`

Expected: FAIL；当前 `<p>` 只能生成纯文本，找不到独立的 `承接预算` 或 Markdown 语义节点。

### Task 2: 复用共享 Markdown 组件

**Files:**
- Modify: `web/src/components/BatchRunView.tsx:1-330`
- Test: `web/src/App.batch.test.tsx`

- [ ] **Step 1: 导入共享组件**

在 `BatchRunView.tsx` 的组件导入区加入：

```tsx
import { MarkdownMessage } from "./MarkdownMessage";
```

- [ ] **Step 2: 替换纯文本回答节点**

将：

```tsx
{openQuestion.response && <p>{openQuestion.response.answer}</p>}
```

替换为：

```tsx
{openQuestion.response && (
  <MarkdownMessage content={openQuestion.response.answer} />
)}
```

- [ ] **Step 3: 运行目标测试并确认 GREEN**

Run: `cd web && npm test -- --run src/App.batch.test.tsx -t "批量会话轮询进度直到终态并展示行状态"`

Expected: PASS。

- [ ] **Step 4: 运行完整前端测试与生产构建**

Run: `cd web && npm test -- --run`

Expected: PASS，全部前端测试通过。

Run: `cd web && npm run build`

Expected: exit code 0。

- [ ] **Step 5: 检查并提交**

Run: `git diff --check && git diff -- web/src/App.batch.test.tsx web/src/components/BatchRunView.tsx`

Expected: 无空白错误，业务差异仅包含 Markdown 渲染及测试。

```bash
git add web/src/App.batch.test.tsx web/src/components/BatchRunView.tsx
git add -f docs/superpowers/plans/2026-07-13-batch-answer-markdown.md
git commit -m "fix: render batch answers as markdown"
```
