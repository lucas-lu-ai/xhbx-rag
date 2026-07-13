# Right-Align Chat Send Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让知识问答输入区的发送按钮在桌面端靠最右显示，同时保留移动端整行宽度布局。

**Architecture:** 保持 React 组件与发送逻辑不变，只调整现有 `.form-actions` Flex 容器的主轴对齐方式。新增一个读取真实样式文件的 Vitest 回归测试，避免布局规则被后续样式改动移除。

**Tech Stack:** CSS、Vitest、TypeScript、Vite

---

### Task 1: 发送按钮响应式对齐

**Files:**
- Create: `web/src/ChatFormLayout.test.ts`
- Modify: `web/src/styles.css:723-727`

- [ ] **Step 1: 写入桌面端右对齐回归测试**

创建 `web/src/ChatFormLayout.test.ts`：

```ts
// @ts-expect-error Vitest runs in Node, while the app tsconfig intentionally omits Node types.
const nodeFs = await import("node:fs");
const styles = (
  nodeFs as { readFileSync: (path: string, encoding: "utf8") => string }
).readFileSync("src/styles.css", "utf8");

test("问答发送按钮操作栏在桌面端靠右对齐", () => {
  expect(styleBlock(".form-actions")).toContain("justify-content: flex-end;");
});

function styleBlock(selector: string): string {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`${escapedSelector}\\s*\\{([^}]*)\\}`, "m").exec(
    styles
  );
  if (!match) {
    throw new Error(`Missing CSS block for ${selector}`);
  }
  return match[1];
}
```

- [ ] **Step 2: 运行测试并确认按预期失败**

Run: `cd web && npm test -- --run src/ChatFormLayout.test.ts`

Expected: FAIL，提示 `.form-actions` 样式块不包含 `justify-content: flex-end;`。

- [ ] **Step 3: 实现最小样式改动**

将 `web/src/styles.css` 中的操作栏样式更新为：

```css
.form-actions {
  display: flex;
  align-items: end;
  justify-content: flex-end;
  gap: 10px;
}
```

不修改 `@media (max-width: 920px)` 中 `.form-actions` 的 `align-items: stretch` 与 `flex-direction: column`，因此移动端按钮仍占满可用宽度。

- [ ] **Step 4: 运行布局测试并确认通过**

Run: `cd web && npm test -- --run src/ChatFormLayout.test.ts`

Expected: PASS，1 项测试通过。

- [ ] **Step 5: 运行完整前端测试与生产构建**

Run: `cd web && npm test -- --run && npm run build`

Expected: 现有前端测试全部通过，TypeScript 与 Vite 生产构建成功。

- [ ] **Step 6: 检查并提交改动**

```bash
git diff --check
git add web/src/ChatFormLayout.test.ts web/src/styles.css docs/superpowers/plans/2026-07-13-right-align-chat-send-button.md
git commit -m "fix: right-align chat send button"
```
