# 证据反馈保存后锁定 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 证据的“应该用”或“不该用”反馈保存成功后锁定两个选项，保存失败时仍允许重试。

**Architecture:** 锁定状态属于单个 `EvidenceDetail` 实例，使用组件本地布尔状态记录提交是否成功。父组件继续负责选中态和反馈载荷，后端接口及数据结构保持不变。

**Tech Stack:** React、TypeScript、Vitest、Testing Library、user-event

---

### Task 1: 用回归测试定义保存后的锁定行为

**Files:**
- Modify: `web/src/components/EvidenceDetail.test.tsx`

- [ ] **Step 1: 扩展“应该用”成功测试，断言两个选项均锁定且不会重复提交**

在现有 `点击应该用立即选中并落地正向 bad case` 测试的成功断言后加入：

```tsx
expect(screen.getByLabelText("证据 1 应该用")).toBeDisabled();
expect(screen.getByLabelText("证据 1 不该用")).toBeDisabled();
await user.click(screen.getByLabelText("证据 1 不该用"));
expect(onSubmitUseful).toHaveBeenCalledTimes(1);
expect(screen.queryByLabelText("不可用理由")).not.toBeInTheDocument();
```

- [ ] **Step 2: 扩展“不该用”成功测试，断言两个选项均锁定**

在现有 `点击不该用立即选中并展开理由输入，保存后提交理由` 测试的成功断言后加入：

```tsx
expect(screen.getByLabelText("证据 1 应该用")).toBeDisabled();
expect(screen.getByLabelText("证据 1 不该用")).toBeDisabled();
```

- [ ] **Step 3: 新增保存失败后不锁定的测试**

```tsx
test("反馈保存失败后不锁定选项并允许重试", async () => {
  const user = userEvent.setup();
  const onSubmitUseful = vi
    .fn()
    .mockRejectedValueOnce(new Error("保存失败"))
    .mockResolvedValueOnce(undefined);
  render(<FeedbackHarness onSubmitUseful={onSubmitUseful} />);

  await user.click(screen.getByLabelText("证据 1 应该用"));

  expect(screen.getByLabelText("证据 1 应该用")).not.toBeDisabled();
  expect(screen.getByLabelText("证据 1 不该用")).not.toBeDisabled();

  await user.click(screen.getByLabelText("证据 1 应该用"));
  await user.click(screen.getByLabelText("证据 1 应该用"));

  expect(onSubmitUseful).toHaveBeenCalledTimes(2);
  expect(await screen.findByText("已记录可用反馈。")).toBeInTheDocument();
  expect(screen.getByLabelText("证据 1 应该用")).toBeDisabled();
  expect(screen.getByLabelText("证据 1 不该用")).toBeDisabled();
});
```

- [ ] **Step 4: 运行目标测试并确认 RED**

Run: `cd web && npm test -- --run src/components/EvidenceDetail.test.tsx`

Expected: FAIL；成功保存后的两个 checkbox 仍未禁用，证明测试覆盖当前缺陷。

### Task 2: 实现保存成功后的本地锁定

**Files:**
- Modify: `web/src/components/EvidenceDetail.tsx`
- Test: `web/src/components/EvidenceDetail.test.tsx`

- [ ] **Step 1: 增加已保存状态**

在 `saving` 状态旁加入：

```tsx
const [feedbackSaved, setFeedbackSaved] = useState(false);
```

- [ ] **Step 2: 仅在两条提交路径成功后锁定**

在 `handleUsefulToggle` 的 `await onSubmitUseful()` 后加入：

```tsx
setFeedbackSaved(true);
```

在 `handleNotUsefulSubmit` 的 `await onSubmitNotUseful(trimmed)` 后加入：

```tsx
setFeedbackSaved(true);
```

异常路径保持不变，不设置锁定状态。

- [ ] **Step 3: 将两个反馈选项的禁用条件统一为保存中或已保存**

两个 checkbox 均使用：

```tsx
disabled={saving || feedbackSaved}
```

- [ ] **Step 4: 运行目标测试并确认 GREEN**

Run: `cd web && npm test -- --run src/components/EvidenceDetail.test.tsx`

Expected: PASS，全部 `EvidenceDetail` 测试通过。

- [ ] **Step 5: 运行前端完整测试和类型检查**

Run: `cd web && npm test -- --run`

Expected: PASS，前端测试全部通过。

Run: `cd web && npm run build`

Expected: exit code 0，TypeScript 与 Vite 构建成功。

- [ ] **Step 6: 检查改动并提交**

Run: `git diff --check && git diff -- web/src/components/EvidenceDetail.tsx web/src/components/EvidenceDetail.test.tsx`

Expected: `git diff --check` 无输出；diff 仅包含反馈锁定实现及对应测试。

```bash
git add web/src/components/EvidenceDetail.tsx web/src/components/EvidenceDetail.test.tsx docs/superpowers/plans/2026-07-13-evidence-feedback-lock.md
git commit -m "fix: lock saved evidence feedback"
```
