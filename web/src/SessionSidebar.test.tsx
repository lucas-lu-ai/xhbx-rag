import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "./App";
import {
  batchRunDetail,
  batchRunSummary,
  installFetchStub,
  installStorageStub,
  jsonResponse,
  runRegisteredCleanups
} from "./test-utils";

beforeEach(() => {
  installStorageStub();
});

afterEach(() => {
  runRegisteredCleanups();
  vi.unstubAllGlobals();
});

// @ts-expect-error Vitest runs in Node, while the app tsconfig intentionally omits Node types.
const nodeFs = await import("node:fs");
const styles = (
  nodeFs as { readFileSync: (path: string, encoding: "utf8") => string }
).readFileSync("src/styles.css", "utf8");

function seedChatSessions() {
  localStorage.setItem(
    "xhbx-rag.chat-sessions.v1",
    JSON.stringify({
      version: 1,
      active_session_id: "session-new",
      sessions: [
        {
          id: "session-new",
          title: "最新聊天",
          created_at: "2026-07-02T09:00:00.000Z",
          updated_at: "2026-07-02T09:00:00.000Z",
          turns: []
        },
        {
          id: "session-old",
          title: "较早聊天",
          created_at: "2026-07-01T08:00:00.000Z",
          updated_at: "2026-07-01T08:00:00.000Z",
          turns: []
        }
      ]
    })
  );
}

test("侧栏按 created_at 降序混排聊天与批量会话并展示徽标和进度", async () => {
  seedChatSessions();
  installFetchStub((url, init) => {
    if (url.endsWith("/api/batch-runs") && (init?.method ?? "GET") === "GET") {
      return jsonResponse({
        runs: [
          batchRunSummary({
            run_id: "run-mid",
            title: "批量问题集",
            status: "running",
            question_total: 10,
            question_done: 2,
            question_failed: 1,
            created_at: "2026-07-01T12:00:00.000Z"
          })
        ]
      });
    }
    return null;
  });
  render(<App listPollIntervalMs={60000} />);

  const sidebar = await screen.findByRole("navigation", { name: "历史会话" });
  await within(sidebar).findByText("批量问题集");

  const items = within(sidebar)
    .getAllByRole("button")
    .filter((item) => item.hasAttribute("aria-pressed"));
  const names = items.map((item) => item.textContent ?? "");
  expect(names[0]).toContain("最新聊天");
  expect(names[1]).toContain("批量问题集");
  expect(names[2]).toContain("较早聊天");

  expect(within(sidebar).getByText("批量")).toBeInTheDocument();
  expect(names[1]).toContain("运行中");
  expect(names[1]).toContain("3/10");
});

test("侧栏新建会话和批量执行按钮使用一致的操作尺寸", async () => {
  installFetchStub();
  render(<App />);

  const newSessionButton = await screen.findByRole("button", { name: "新会话" });
  const createBatchButton = screen.getByRole("button", { name: "批量执行" });

  expect(newSessionButton).toHaveClass("session-new-button");
  expect(createBatchButton).toHaveClass("session-new-button");
  expect(styleBlock(".session-new-button")).toContain("flex: 0 0 104px;");
  expect(styleBlock(".session-new-button")).toContain("width: 104px;");
  expect(styleBlock(".session-new-button")).toContain("min-height: 44px;");
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

test("删除批量会话成功后从列表移除并回退到最新条目", async () => {
  const user = userEvent.setup();
  seedChatSessions();
  installStorageStub().setItem(
    "xhbx-rag.active-session.v1",
    JSON.stringify({ kind: "batch", id: "run-1" })
  );
  seedChatSessions();
  let deleted = false;
  const { requests } = installFetchStub((url, init) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/batch-runs") && method === "GET") {
      return jsonResponse({
        runs: deleted
          ? []
          : [batchRunSummary({ created_at: "2026-07-01T12:00:00.000Z" })]
      });
    }
    if (url.endsWith("/api/batch-runs/run-1") && method === "DELETE") {
      deleted = true;
      return jsonResponse({ ok: true });
    }
    if (url.endsWith("/api/batch-runs/run-1")) {
      return jsonResponse(batchRunDetail());
    }
    return null;
  });
  render(<App />);

  expect(
    await within(
      screen.getByRole("main", { name: "RAG 问答" })
    ).findByRole("button", { name: /客户说每年不能超过80万怎么办？/ })
  ).toBeInTheDocument();

  await user.click(
    await screen.findByRole("button", { name: "删除批量会话 批量测试" })
  );

  await waitFor(() => {
    expect(screen.queryAllByText("批量测试")).toHaveLength(0);
  });
  expect(requests).toContainEqual(
    expect.objectContaining({
      url: "/api/batch-runs/run-1",
      method: "DELETE"
    })
  );
  // 删除的是当前选中批量会话，回退到合并列表最新条目（最新聊天会话）。
  expect(screen.getByLabelText("输入问题")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /最新聊天.*轮/ })
  ).toHaveAttribute("aria-pressed", "true");
});

test("删除运行中的批量会话返回 409 时保持选中并展示后端 detail", async () => {
  const user = userEvent.setup();
  seedChatSessions();
  installStorageStub().setItem(
    "xhbx-rag.active-session.v1",
    JSON.stringify({ kind: "batch", id: "run-1" })
  );
  seedChatSessions();
  installFetchStub((url, init) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/batch-runs") && method === "GET") {
      return jsonResponse({
        runs: [batchRunSummary({ created_at: "2026-07-01T12:00:00.000Z" })]
      });
    }
    if (url.endsWith("/api/batch-runs/run-1") && method === "DELETE") {
      return jsonResponse(
        { detail: "批量任务正在执行，无法删除" },
        { status: 409 }
      );
    }
    if (url.endsWith("/api/batch-runs/run-1")) {
      return jsonResponse(batchRunDetail());
    }
    return null;
  });
  render(<App />);

  const qaPanel = screen.getByRole("main", { name: "RAG 问答" });
  expect(
    await within(qaPanel).findByRole("button", {
      name: /客户说每年不能超过80万怎么办？/
    })
  ).toBeInTheDocument();

  await user.click(
    await screen.findByRole("button", { name: "删除批量会话 批量测试" })
  );

  expect(
    await screen.findByText("批量任务正在执行，无法删除")
  ).toBeInTheDocument();
  expect(screen.getAllByText("批量测试").length).toBeGreaterThan(0);
  expect(
    within(qaPanel).getByRole("button", {
      name: /客户说每年不能超过80万怎么办？/
    })
  ).toBeInTheDocument();
});

test("刷新后恢复选中的批量会话", async () => {
  seedChatSessions();
  installStorageStub().setItem(
    "xhbx-rag.active-session.v1",
    JSON.stringify({ kind: "batch", id: "run-1" })
  );
  seedChatSessions();
  installFetchStub((url, init) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/batch-runs") && method === "GET") {
      return jsonResponse({
        runs: [batchRunSummary({ created_at: "2026-07-01T12:00:00.000Z" })]
      });
    }
    if (url.endsWith("/api/batch-runs/run-1")) {
      return jsonResponse(batchRunDetail());
    }
    return null;
  });
  render(<App />);

  expect(
    await within(
      screen.getByRole("main", { name: "RAG 问答" })
    ).findByRole("button", { name: /客户说每年不能超过80万怎么办？/ })
  ).toBeInTheDocument();
  const sidebar = screen.getByRole("navigation", { name: "历史会话" });
  const batchItem = within(sidebar)
    .getAllByRole("button")
    .filter((item) => item.hasAttribute("aria-pressed"))
    .find((item) => (item.textContent ?? "").includes("批量测试"));
  expect(batchItem).toHaveAttribute("aria-pressed", "true");
});

test("恢复的批量会话不存在时回退到最新聊天会话", async () => {
  seedChatSessions();
  installStorageStub().setItem(
    "xhbx-rag.active-session.v1",
    JSON.stringify({ kind: "batch", id: "run-missing" })
  );
  seedChatSessions();
  installFetchStub();
  render(<App />);

  expect(await screen.findByLabelText("输入问题")).toBeInTheDocument();
  await waitFor(() => {
    expect(
      screen.getByRole("button", { name: /最新聊天.*轮/ })
    ).toHaveAttribute("aria-pressed", "true");
  });
});

test("批量会话列表加载失败时侧栏降级为只显示聊天会话", async () => {
  seedChatSessions();
  installFetchStub((url, init) => {
    if (url.endsWith("/api/batch-runs") && (init?.method ?? "GET") === "GET") {
      return jsonResponse({ detail: "批量任务存储不可用" }, { status: 500 });
    }
    return null;
  });
  render(<App />);

  expect(await screen.findByLabelText("输入问题")).toBeInTheDocument();
  expect(
    await screen.findByText("批量会话列表加载失败，仅显示聊天会话。")
  ).toBeInTheDocument();
  const sidebar = screen.getByRole("navigation", { name: "历史会话" });
  expect(within(sidebar).getByText("最新聊天")).toBeInTheDocument();
  expect(within(sidebar).getByText("较早聊天")).toBeInTheDocument();
});
