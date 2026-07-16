# 延迟创建新聊天会话实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 点击“新会话”只打开未持久化的空白草稿，并在首次发送非空问题时才创建正式历史会话。

**Architecture:** `App` 持有不进入 `sessionStore` 的内存草稿，并让草稿成为当前可见聊天但不成为持久化选择。`ChatView` 把首个 turn 的创建与后续流式更新分开，使 `App` 能在首个 turn 到来时原子地把草稿插入正式会话仓库；历史会话仍沿用现有更新路径。

**Tech Stack:** React 19、TypeScript、Vitest、Testing Library、浏览器 `localStorage`

---

## 文件结构

- 修改 `web/src/App.chat.test.tsx`：覆盖点击新会话不新增、不持久化、首次发送后新增，以及放弃草稿不留残余。
- 修改 `web/src/App.tsx`：拥有草稿生命周期、可见选择、草稿转正式会话和草稿取消逻辑。
- 修改 `web/src/components/ChatView.tsx`：通过独立的 `onStartTurn` 回调提交首个流式 turn，后续事件继续使用 `onUpdateSession`。
- 不修改 `web/src/chatSessions.ts`、存储版本或后端接口。

### Task 1：用集成测试锁定延迟创建契约

**Files:**
- Modify: `web/src/App.chat.test.tsx:103-201`
- Test: `web/src/App.chat.test.tsx`

- [ ] **Step 1：把现有创建与切换测试改为先验证草稿不进入列表和存储**

在 `creates and switches sessions` 测试中，首次问答完成后记录历史条目和持久化快照；点击“新会话”后断言两者未增加，再发送第二个问题并断言此时新增正式会话：

```tsx
test("creates a clicked new session only after its first question is submitted", async () => {
  const user = userEvent.setup();
  installFetchStub();
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));
  expect(
    await screen.findByText("先承接预算，再讨论缴费期和保障缺口。")
  ).toBeInTheDocument();

  const sidebar = screen.getByRole("navigation", { name: "历史会话" });
  const persistedBeforeDraft = localStorage.getItem("xhbx-rag.chat-sessions.v1");
  const sessionRows = () =>
    within(sidebar)
      .getAllByRole("button")
      .filter((button) => button.hasAttribute("aria-pressed"));
  expect(sessionRows()).toHaveLength(1);

  await user.click(screen.getByRole("button", { name: "新会话" }));

  expect(screen.getByText("暂无问答")).toBeInTheDocument();
  expect(sessionRows()).toHaveLength(1);
  expect(sessionRows()[0]).toHaveAttribute("aria-pressed", "false");
  expect(localStorage.getItem("xhbx-rag.chat-sessions.v1")).toBe(
    persistedBeforeDraft
  );

  await user.type(screen.getByLabelText("输入问题"), "保单整理有什么作用？");
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(
    await within(sidebar).findByRole("button", { name: /保单整理有什么作用？.*1 轮/ })
  ).toBeInTheDocument();
  expect(sessionRows()).toHaveLength(2);
  const stored = JSON.parse(
    localStorage.getItem("xhbx-rag.chat-sessions.v1") ?? ""
  );
  expect(stored.sessions).toHaveLength(2);
  expect(stored.sessions[0]).toMatchObject({
    title: "保单整理有什么作用？"
  });

  await user.click(
    within(sidebar).getByRole("button", {
      name: /客户说每年不能超过80万怎么办？.*1 轮/
    })
  );
  expect(
    within(screen.getByRole("main", { name: "RAG 问答" })).getByText(
      "客户说每年不能超过80万怎么办？"
    )
  ).toBeInTheDocument();
});
```

- [ ] **Step 2：增加未发送草稿切回历史会话的回归测试**

```tsx
test("abandons an unsent new-session draft without persisting it", async () => {
  const user = userEvent.setup();
  installFetchStub();
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户预算有限怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));
  const historyButton = await screen.findByRole("button", {
    name: /客户预算有限怎么办？.*1 轮/
  });
  const persistedBeforeDraft = localStorage.getItem("xhbx-rag.chat-sessions.v1");

  await user.click(screen.getByRole("button", { name: "新会话" }));
  await user.type(screen.getByLabelText("输入问题"), "这段文字不应保存");
  await user.click(historyButton);

  expect(screen.getByText("客户预算有限怎么办？")).toBeInTheDocument();
  expect(localStorage.getItem("xhbx-rag.chat-sessions.v1")).toBe(
    persistedBeforeDraft
  );
  const stored = JSON.parse(
    localStorage.getItem("xhbx-rag.chat-sessions.v1") ?? ""
  );
  expect(stored.sessions).toHaveLength(1);
});
```

- [ ] **Step 3：运行测试并确认按预期失败**

Run:

```bash
cd web
npm test -- --run src/App.chat.test.tsx
```

Expected: 新的延迟创建断言失败；当前实现点击按钮后会出现第二个带 `aria-pressed` 的历史条目，并改变 `xhbx-rag.chat-sessions.v1`。

### Task 2：实现内存草稿和首次 turn 转正

**Files:**
- Modify: `web/src/App.tsx:20-34,133-139,501-534,642-725,1082-1127`
- Modify: `web/src/components/ChatView.tsx:28-44,60-78`
- Test: `web/src/App.chat.test.tsx`

- [ ] **Step 1：让 `ChatView` 用独立回调开始 turn**

在 `ChatViewProps` 增加：

```tsx
onStartTurn: (
  sessionId: string,
  turn: ChatTurn,
  title?: string
) => void;
```

从参数中接收 `onStartTurn`，然后把 `handleSubmit` 中首次更新改为：

```tsx
const id = makeTurnId();
const submittedSessionId = session.id;
onStartTurn(
  submittedSessionId,
  makeStreamingTurn(id, trimmed, topN, topK),
  sessionTitleForQuestion(session, trimmed)
);
setQuery("");
```

思考增量、回答增量、完成、失败和清空仍调用 `onUpdateSession`，避免扩大接口改动。

- [ ] **Step 2：在 `App` 中增加草稿状态并让其成为可见会话**

在类型导入中加入 `ChatSession`，并在 `sessionStore` 后新增：

```tsx
const [draftSession, setDraftSession] = useState<ChatSession | null>(null);
```

在 `effectiveSelection` 最前面优先返回草稿选择，并把 `draftSession` 加入依赖：

```tsx
if (draftSession) {
  return { kind: "chat", id: draftSession.id };
}
```

在 `activeChatSession` 最前面返回匹配的草稿：

```tsx
if (
  draftSession &&
  effectiveSelection.kind === "chat" &&
  effectiveSelection.id === draftSession.id
) {
  return draftSession;
}
```

持久化选择时跳过草稿，避免刷新后恢复一个从未落盘的 id：

```tsx
useEffect(() => {
  if (!draftSession) {
    persistSessionSelection(effectiveSelection);
  }
}, [draftSession, effectiveSelection]);
```

- [ ] **Step 3：实现草稿创建、取消和首次 turn 转正**

`selectSession` 和 `startBatchCreate` 在进入其他内容前调用：

```tsx
setDraftSession(null);
```

把 `createSession` 改为只创建内存草稿并重置界面状态：

```tsx
function createSession() {
  setDraftSession(createEmptySession());
  setCreatingBatch(false);
  setDeleteError("");
  resetEvidenceSelection();
}
```

在 `updateSessionTurns` 前增加首次 turn 处理器：

```tsx
function startSessionTurn(
  sessionId: string,
  turn: ChatTurn,
  title?: string
) {
  const now = new Date().toISOString();
  setSessionStore((current) => {
    const updated = updateSession(current, sessionId, (session) => ({
      ...session,
      title: title ?? session.title,
      turns: [...session.turns, turn],
      updated_at: now
    }));
    if (updated !== current) {
      return updated;
    }
    if (!draftSession || draftSession.id !== sessionId) {
      return current;
    }
    const session: ChatSession = {
      ...draftSession,
      title: title ?? draftSession.title,
      turns: [turn],
      updated_at: now
    };
    return {
      ...current,
      active_session_id: session.id,
      sessions: [session, ...current.sessions]
    };
  });
  if (draftSession?.id === sessionId) {
    setDraftSession(null);
    setSelection({ kind: "chat", id: sessionId });
  }
}
```

向 `ChatView` 传入：

```tsx
onStartTurn={startSessionTurn}
```

- [ ] **Step 4：运行聊天集成测试并确认通过**

Run:

```bash
cd web
npm test -- --run src/App.chat.test.tsx
```

Expected: `App.chat.test.tsx` 全部通过，新的草稿测试由红转绿。

- [ ] **Step 5：检查改动范围**

Run:

```bash
git diff -- web/src/App.tsx web/src/components/ChatView.tsx web/src/App.chat.test.tsx
git diff --check
```

Expected: 只有草稿生命周期、首个 turn 回调及对应测试发生变化；`git diff --check` 无输出。

### Task 3：完整回归验证并提交

**Files:**
- Verify: `web/src/**/*.test.ts`, `web/src/**/*.test.tsx`
- Verify: `web/src/App.tsx`, `web/src/components/ChatView.tsx`

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

Expected: TypeScript 检查和 Vite 构建成功。

- [ ] **Step 3：检查最终工作区**

Run:

```bash
git diff --check
git status --short
```

Expected: 只有计划内前端文件和本计划文档有改动，且无空白错误。

- [ ] **Step 4：提交实现**

```bash
git add web/src/App.tsx web/src/components/ChatView.tsx web/src/App.chat.test.tsx
git commit -m "fix: defer new chat session creation"
```

Expected: 提交成功，提交内容仅包含延迟创建实现和回归测试。
