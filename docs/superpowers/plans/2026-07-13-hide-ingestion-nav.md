# 隐藏文档入库导航按钮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 隐藏工作台导航中的“文档入库”按钮，同时保留通过 URL 访问文档入库页面及其全部功能。

**Architecture:** 仅从共享 `WorkspaceNav` 的 JSX 中移除文档入库入口。路由类型、路由解析、页面组件和后端接口不变；依赖按钮导航的集成测试改用已有 `?view=ingestion` URL 进入。

**Tech Stack:** React、TypeScript、Vitest、Testing Library

---

### Task 1: 定义导航入口隐藏行为

**Files:**
- Create: `web/src/components/WorkspaceNav.test.tsx`

- [ ] **Step 1: 添加失败测试**

```tsx
import { render, screen } from "@testing-library/react";

import { WorkspaceNav } from "./WorkspaceNav";

test("只显示知识问答入口并隐藏文档入库按钮", () => {
  render(<WorkspaceNav currentView="chat" onNavigate={() => {}} />);

  expect(screen.getByRole("button", { name: "知识问答" })).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "文档入库" })
  ).not.toBeInTheDocument();
});
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `cd web && npm test -- --run src/components/WorkspaceNav.test.tsx`

Expected: FAIL；当前仍渲染“文档入库”按钮。

### Task 2: 移除入口并保留 URL 功能覆盖

**Files:**
- Modify: `web/src/components/WorkspaceNav.tsx`
- Modify: `web/src/App.ingestion.test.tsx:20-50`
- Modify: `web/src/App.ingestion.test.tsx:1500-1520`
- Test: `web/src/components/WorkspaceNav.test.tsx`

- [ ] **Step 1: 从共享导航移除文档入库按钮**

将图标导入改为：

```tsx
import { MessageSquareText } from "lucide-react";
```

删除调用 `onNavigate("ingestion")` 的整个按钮 JSX，保留“知识问答”按钮。

- [ ] **Step 2: 将上传预检测试改为通过 URL 进入**

在 `render(<App />)` 前加入：

```tsx
window.history.replaceState(null, "", "/?view=ingestion");
```

删除：

```tsx
await user.click(screen.getByRole("button", { name: "文档入库" }));
expect(window.location.search).toBe("?view=ingestion");
```

- [ ] **Step 3: 将上传期间测试改为通过 URL 进入**

在 `render(<App />)` 前加入：

```tsx
window.history.replaceState(null, "", "/?view=ingestion");
```

删除：

```tsx
await user.click(screen.getByRole("button", { name: "文档入库" }));
```

- [ ] **Step 4: 运行相关测试并确认 GREEN**

Run: `cd web && npm test -- --run src/components/WorkspaceNav.test.tsx src/App.ingestion.test.tsx`

Expected: PASS；入口隐藏且 URL 进入的文档入库功能测试通过。

- [ ] **Step 5: 运行完整测试与生产构建**

Run: `cd web && npm test -- --run`

Expected: PASS。

Run: `cd web && npm run build`

Expected: exit code 0。

- [ ] **Step 6: 检查并提交**

Run: `git diff --check && git diff -- web/src/components/WorkspaceNav.tsx web/src/components/WorkspaceNav.test.tsx web/src/App.ingestion.test.tsx`

Expected: 业务差异仅包含入口隐藏及相应测试调整。

```bash
git add web/src/components/WorkspaceNav.tsx web/src/components/WorkspaceNav.test.tsx web/src/App.ingestion.test.tsx
git add -f docs/superpowers/plans/2026-07-13-hide-ingestion-nav.md
git commit -m "fix: hide document ingestion navigation"
```
