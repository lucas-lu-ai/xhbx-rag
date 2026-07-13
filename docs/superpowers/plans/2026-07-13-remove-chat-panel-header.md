# 删除知识问答顶部标题栏 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除知识问答主面板顶部标题栏及其占位，同时保留文档入库页面标题栏。

**Architecture:** 仅删除 `App` 聊天分支中的 `panel-header` JSX。共享样式和文档入库分支不变，通过聊天与入库集成测试分别验证删除范围。

**Tech Stack:** React、TypeScript、Vitest、Testing Library

---

### Task 1: 定义知识问答标题移除边界

**Files:**
- Modify: `web/src/App.chat.test.tsx:20-35`
- Modify: `web/src/App.ingestion.test.tsx:25-50`

- [ ] **Step 1: 在聊天测试中断言标题不存在**

在 `uses default retrieval and citation limits` 测试的 `render(<App />)` 后加入：

```tsx
const qaPanel = screen.getByRole("main", { name: "RAG 问答" });
expect(within(qaPanel).queryByText("销售知识库问答")).not.toBeInTheDocument();
expect(within(qaPanel).queryByText("xhbx-rag Web")).not.toBeInTheDocument();
```

- [ ] **Step 2: 在文档入库测试中断言独立标题保留**

在 `打开入库工作台后上传、预检并确认 draft` 测试渲染后加入：

```tsx
expect(
  screen.getByRole("heading", { name: "文档入库工作台" })
).toBeInTheDocument();
```

- [ ] **Step 3: 运行聊天目标测试并确认 RED**

Run: `cd web && npm test -- --run src/App.chat.test.tsx -t "uses default retrieval and citation limits"`

Expected: FAIL；知识问答标题当前仍存在。

### Task 2: 删除知识问答标题节点

**Files:**
- Modify: `web/src/App.tsx:1145-1160`
- Test: `web/src/App.chat.test.tsx`
- Test: `web/src/App.ingestion.test.tsx`

- [ ] **Step 1: 删除聊天分支中的完整标题节点**

删除：

```tsx
<header className="panel-header">
  <div>
    <p className="eyebrow">xhbx-rag Web</p>
    <h1>销售知识库问答</h1>
  </div>
</header>
```

保留其后的状态提示及内容分支。

- [ ] **Step 2: 运行聊天与入库相关测试并确认 GREEN**

Run: `cd web && npm test -- --run src/App.chat.test.tsx src/App.ingestion.test.tsx`

Expected: PASS；知识问答标题不存在，文档入库标题仍存在。

- [ ] **Step 3: 运行完整前端测试与生产构建**

Run: `cd web && npm test -- --run`

Expected: PASS。

Run: `cd web && npm run build`

Expected: exit code 0。

- [ ] **Step 4: 检查并提交**

Run: `git diff --check && git diff -- web/src/App.tsx web/src/App.chat.test.tsx web/src/App.ingestion.test.tsx`

Expected: 业务差异仅包含聊天标题删除及范围测试。

```bash
git add web/src/App.tsx web/src/App.chat.test.tsx web/src/App.ingestion.test.tsx
git add -f docs/superpowers/plans/2026-07-13-remove-chat-panel-header.md
git commit -m "fix: remove chat panel header"
```
