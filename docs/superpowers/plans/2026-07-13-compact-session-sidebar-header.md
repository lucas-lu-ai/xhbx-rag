# 会话侧栏头部紧凑布局 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将知识问答导航居中，删除会话标题，并让新会话与批量执行按钮在所有断点下同排等宽。

**Architecture:** 保持现有 React 组件边界，仅精简 `SessionSidebar` 标题 JSX，并调整共享导航与会话操作区 CSS。使用已有 CSS 文本测试锁定宽度、间距和最小点击高度。

**Tech Stack:** React、TypeScript、CSS、Vitest、Testing Library

---

### Task 1: 定义紧凑侧栏布局

**Files:**
- Modify: `web/src/SessionSidebar.test.tsx:85-110`
- Modify: `web/src/components/WorkspaceNav.test.tsx`

- [ ] **Step 1: 扩展侧栏测试以断言标题删除和操作按钮布局**

将现有操作尺寸测试扩展为：

```tsx
expect(screen.queryByText("会话")).not.toBeInTheDocument();
expect(screen.queryByText("问答会话")).not.toBeInTheDocument();
expect(styleBlock(".session-header-actions")).toContain("flex-wrap: nowrap;");
expect(styleBlock(".session-header-actions")).toContain("width: 100%;");
expect(styleBlock(".session-new-button")).toContain("flex: 1 1 0;");
expect(styleBlock(".session-new-button")).toContain("width: auto;");
expect(styleBlock(".session-new-button")).toContain("min-height: 44px;");
```

移除旧的固定 `104px` 宽度断言。

- [ ] **Step 2: 扩展导航测试以断言居中和半栏宽度**

在 `WorkspaceNav.test.tsx` 中读取 `styles.css` 并加入 `styleBlock` 辅助函数，然后断言：

```tsx
expect(styleBlock(".workspace-nav")).toContain("display: flex;");
expect(styleBlock(".workspace-nav")).toContain("justify-content: center;");
expect(styleBlock(".workspace-nav-item")).toContain(
  "width: calc((100% - 8px) / 2);"
);
```

- [ ] **Step 3: 运行目标测试并确认 RED**

Run: `cd web && npm test -- --run src/SessionSidebar.test.tsx src/components/WorkspaceNav.test.tsx`

Expected: FAIL；当前仍显示标题，导航仍使用双列网格，按钮仍为固定宽度。

### Task 2: 实现紧凑并排布局

**Files:**
- Modify: `web/src/components/SessionSidebar.tsx:40-75`
- Modify: `web/src/styles.css:150-180`
- Modify: `web/src/styles.css:1915-1950`
- Modify: `web/src/styles.css:1980-2010`
- Test: `web/src/SessionSidebar.test.tsx`
- Test: `web/src/components/WorkspaceNav.test.tsx`

- [ ] **Step 1: 删除会话标题文案节点**

从 `session-header` 内删除：

```tsx
<div>
  <p className="eyebrow">会话</p>
  <h2>问答会话</h2>
</div>
```

仅保留 `session-header-actions`。

- [ ] **Step 2: 将导航改为半栏宽度居中**

`.workspace-nav` 使用：

```css
display: flex;
justify-content: center;
```

删除双列网格声明；`.workspace-nav-item` 增加：

```css
width: calc((100% - 8px) / 2);
```

- [ ] **Step 3: 将两个操作按钮改为同排等宽**

更新基础样式：

```css
.session-header-actions {
  display: flex;
  flex-wrap: nowrap;
  justify-content: center;
  gap: 8px;
  width: 100%;
}

.session-new-button {
  flex: 1 1 0;
  width: auto;
  min-width: 0;
  min-height: 44px;
}
```

- [ ] **Step 4: 移除窄屏纵向覆盖**

在 `max-width: 920px` 规则中，从纵向按钮组选择器删除 `.session-header-actions`，并删除将 `.session-new-button` 设为 `width: 100%` 的覆盖块。

- [ ] **Step 5: 运行相关测试并确认 GREEN**

Run: `cd web && npm test -- --run src/SessionSidebar.test.tsx src/components/WorkspaceNav.test.tsx`

Expected: PASS。

- [ ] **Step 6: 运行完整测试与生产构建**

Run: `cd web && npm test -- --run`

Expected: PASS。

Run: `cd web && npm run build`

Expected: exit code 0。

- [ ] **Step 7: 检查并提交**

Run: `git diff --check && git diff -- web/src/components/SessionSidebar.tsx web/src/styles.css web/src/SessionSidebar.test.tsx web/src/components/WorkspaceNav.test.tsx`

Expected: 业务差异仅包含侧栏头部布局及测试。

```bash
git add web/src/components/SessionSidebar.tsx web/src/styles.css web/src/SessionSidebar.test.tsx web/src/components/WorkspaceNav.test.tsx
git add -f docs/superpowers/plans/2026-07-13-compact-session-sidebar-header.md
git commit -m "fix: compact session sidebar header"
```
