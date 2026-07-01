import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "./App";

const statusPayload = {
  ok: true,
  data_dir: "data",
  milvus_mode: "lite",
  milvus_target: ".local/milvus/xhbx_rag.db",
  milvus_lite_path: ".local/milvus/xhbx_rag.db",
  milvus_collection: "xhbx_sales_chunks",
  config: { API_KEY: true },
  errors: []
};

const answerPayload = {
  answer: "先承接预算，再讨论缴费期和保障缺口。",
  citations: [
    {
      filename: "第1节.track-0.txt",
      source_type: "txt",
      source_path: "data/案例A/第1节.track-0.txt",
      display_location: "L2",
      display_excerpt: "客户说每年保费预算不能超过80万",
      locator_confidence: "validated_span",
      can_reveal: true
    }
  ],
  evidence_count: 1,
  rewritten_query: "客户预算上限80万时如何回应",
  retrieval_evidences: [
    {
      chunk_id: "case-a-2",
      chunk_type: "objection_handling",
      text: "客户担心预算，可以先承接预算，再对齐保障缺口。",
      rerank_score: 0.91,
      metadata: { case_name: "案例A", stage: "需求分析" },
      citations: [
        {
          filename: "第2节.track-0.txt",
          source_type: "txt",
          source_path: "data/案例A/第2节.track-0.txt",
          display_location: "L1",
          display_excerpt: "客户担心预算，可以先承接预算，再对齐保障缺口。",
          can_reveal: true
        }
      ]
    }
  ]
};

function answerPayloadWithCitations(count: number) {
  return {
    ...answerPayload,
    citations: Array.from({ length: count }, (_, index) => ({
      filename: `第${index + 1}节.track-0.txt`,
      source_type: "txt",
      source_path: `data/案例A/第${index + 1}节.track-0.txt`,
      display_location: `L${index + 1}`,
      display_excerpt: `第${index + 1}条引用原文`,
      locator_confidence: "validated_span",
      can_reveal: true
    })),
    evidence_count: count
  };
}

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init
  });
}

function sseResponse(events: Array<{ event: string; data: unknown }>): Response {
  return new Response(
    events
      .map((event) => `event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`)
      .join(""),
    {
      status: 200,
      headers: { "Content-Type": "text/event-stream" }
    }
  );
}

function answerStreamResponse() {
  return sseResponse([
    {
      event: "step",
      data: {
        type: "step",
        step: "search.query_understood",
        message: "已完成问题理解",
        payload: { rewritten_query: answerPayload.rewritten_query }
      }
    },
    {
      event: "step",
      data: {
        type: "step",
        step: "search.reranked",
        message: "已完成证据重排",
        payload: { result_count: answerPayload.evidence_count }
      }
    },
    { event: "answer_delta", data: { type: "answer_delta", text: "先承接预算，" } },
    {
      event: "answer_delta",
      data: { type: "answer_delta", text: "再讨论缴费期和保障缺口。" }
    },
    { event: "final", data: { type: "final", response: answerPayload } }
  ]);
}

function installFetchStub() {
  const requests: Array<{ url: string; body?: unknown }> = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    requests.push({
      url,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined
    });

    if (url.endsWith("/api/status")) {
      return jsonResponse(statusPayload);
    }
    if (url.endsWith("/api/answer/stream")) {
      return answerStreamResponse();
    }
    if (url.endsWith("/api/answer")) {
      return jsonResponse(answerPayload);
    }
    if (url.endsWith("/api/source/reveal")) {
      return jsonResponse({ ok: true, resolved_path: "/tmp/data/a.txt" });
    }
    if (url.endsWith("/api/bad-cases")) {
      return jsonResponse({ ok: true, bad_case_id: "bad-case-1" });
    }
    return jsonResponse({ detail: "not found" }, { status: 404 });
  });

  vi.stubGlobal("fetch", fetcher);
  return { fetcher, requests };
}

function deferredResponse() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function installStorageStub() {
  const store = new Map<string, string>();
  const storage = {
    get length() {
      return store.size;
    },
    clear: vi.fn(() => store.clear()),
    getItem: vi.fn((key: string) => store.get(key) ?? null),
    key: vi.fn((index: number) => [...store.keys()][index] ?? null),
    removeItem: vi.fn((key: string) => {
      store.delete(key);
    }),
    setItem: vi.fn((key: string, value: string) => {
      store.set(key, String(value));
    })
  } satisfies Storage;

  vi.stubGlobal("localStorage", storage);
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: storage
  });
  return storage;
}

test("uses default retrieval and citation limits", async () => {
  installStorageStub();
  installFetchStub();

  render(<App />);

  expect(await screen.findByLabelText("召回数量")).toHaveValue(20);
  expect(screen.getByLabelText("引用数量")).toHaveValue(5);
});

test("parses pasted comma-separated batch questions", async () => {
  const user = userEvent.setup();
  installFetchStub();
  render(<App />);

  await user.click(screen.getByRole("button", { name: "批量" }));
  await user.type(
    screen.getByLabelText("批量问题内容"),
    "问题,答案\n客户说每年不能超过80万怎么办？,\n保单整理有什么作用？,人工答案"
  );
  await user.click(screen.getByRole("button", { name: "解析内容" }));

  expect(screen.getByText("已解析 2 个问题")).toBeInTheDocument();
  expect(screen.getByText("客户说每年不能超过80万怎么办？")).toBeInTheDocument();
  expect(screen.getByText("保单整理有什么作用？")).toBeInTheDocument();
  expect(screen.getByText("人工答案")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "开始批量运行" })).toBeEnabled();
});

test("clears parsed batch questions when pasted content changes", async () => {
  const user = userEvent.setup();
  installFetchStub();
  render(<App />);

  await user.click(screen.getByRole("button", { name: "批量" }));
  await user.type(
    screen.getByLabelText("批量问题内容"),
    "问题,答案\n客户说每年不能超过80万怎么办？,\n保单整理有什么作用？,人工答案"
  );
  await user.click(screen.getByRole("button", { name: "解析内容" }));

  expect(screen.getByText("已解析 2 个问题")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "开始批量运行" })).toBeEnabled();

  await user.type(screen.getByLabelText("批量问题内容"), "\n新增问题,新增答案");

  expect(screen.queryByText("已解析 2 个问题")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "开始批量运行" })).toBeDisabled();
});

beforeEach(() => {
  installStorageStub();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("restores persisted sessions after reload", async () => {
  localStorage.setItem(
    "xhbx-rag.chat-sessions.v1",
    JSON.stringify({
      version: 1,
      active_session_id: "session-2",
      sessions: [
        {
          id: "session-1",
          title: "预算异议",
          created_at: "2026-07-01T08:00:00.000Z",
          updated_at: "2026-07-01T08:00:00.000Z",
          turns: []
        },
        {
          id: "session-2",
          title: "保单整理",
          created_at: "2026-07-01T08:01:00.000Z",
          updated_at: "2026-07-01T08:02:00.000Z",
          turns: [
            {
              id: "turn-1",
              query: "保单整理有什么作用？",
              top_n: 20,
              top_k: 10,
              response: answerPayload
            }
          ]
        }
      ]
    })
  );
  installFetchStub();

  render(<App />);

  expect(
    await screen.findByRole("button", { name: /保单整理.*1 轮/ })
  ).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("保单整理有什么作用？")).toBeInTheDocument();
  expect(screen.getByText("先承接预算，再讨论缴费期和保障缺口。")).toBeInTheDocument();
});

test("creates and switches sessions", async () => {
  const user = userEvent.setup();
  installFetchStub();
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));
  expect(
    await screen.findByText("先承接预算，再讨论缴费期和保障缺口。")
  ).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "新会话" }));

  expect(screen.getByText("暂无问答")).toBeInTheDocument();
  expect(
    screen.queryByText("先承接预算，再讨论缴费期和保障缺口。")
  ).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /1 轮/ }));

  const qaPanel = screen.getByRole("main", { name: "RAG 问答" });
  expect(
    within(qaPanel).getByText("客户说每年不能超过80万怎么办？")
  ).toBeInTheDocument();
  expect(
    within(qaPanel).getByText("先承接预算，再讨论缴费期和保障缺口。")
  ).toBeInTheDocument();
});

test("deletes sessions from persistent storage and keeps an empty fallback", async () => {
  const user = userEvent.setup();
  localStorage.setItem(
    "xhbx-rag.chat-sessions.v1",
    JSON.stringify({
      version: 1,
      active_session_id: "session-2",
      sessions: [
        {
          id: "session-1",
          title: "预算异议",
          created_at: "2026-07-01T08:00:00.000Z",
          updated_at: "2026-07-01T08:00:00.000Z",
          turns: []
        },
        {
          id: "session-2",
          title: "保单整理",
          created_at: "2026-07-01T08:01:00.000Z",
          updated_at: "2026-07-01T08:02:00.000Z",
          turns: [
            {
              id: "turn-1",
              query: "保单整理有什么作用？",
              top_n: 20,
              top_k: 10,
              response: answerPayload
            }
          ]
        }
      ]
    })
  );
  installFetchStub();
  render(<App />);

  await user.click(await screen.findByRole("button", { name: "删除会话 保单整理" }));

  expect(screen.queryByText("保单整理有什么作用？")).not.toBeInTheDocument();
  let stored = JSON.parse(localStorage.getItem("xhbx-rag.chat-sessions.v1") ?? "");
  expect(stored.active_session_id).toBe("session-1");
  expect(stored.sessions.map((session: { id: string }) => session.id)).toEqual([
    "session-1"
  ]);

  await user.click(screen.getByRole("button", { name: "删除会话 预算异议" }));

  expect(screen.getByText("暂无问答")).toBeInTheDocument();
  stored = JSON.parse(localStorage.getItem("xhbx-rag.chat-sessions.v1") ?? "");
  expect(stored.sessions).toHaveLength(1);
  expect(stored.sessions[0]).toMatchObject({ title: "新会话", turns: [] });
  expect(stored.active_session_id).toBe(stored.sessions[0].id);
});

test("titles a new session from the first submitted question and persists it", async () => {
  const user = userEvent.setup();
  installFetchStub();
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(
    await screen.findByRole("button", {
      name: /客户说每年不能超过80万怎么办？.*1 轮/
    })
  ).toBeInTheDocument();
  const stored = JSON.parse(localStorage.getItem("xhbx-rag.chat-sessions.v1") ?? "");
  expect(stored.sessions[0].title).toBe("客户说每年不能超过80万怎么办？");
});

test("collapses long citation lists and toggles all citations", async () => {
  const user = userEvent.setup();
  localStorage.setItem(
    "xhbx-rag.chat-sessions.v1",
    JSON.stringify({
      version: 1,
      active_session_id: "session-1",
      sessions: [
        {
          id: "session-1",
          title: "多引用回答",
          created_at: "2026-07-01T08:00:00.000Z",
          updated_at: "2026-07-01T08:01:00.000Z",
          turns: [
            {
              id: "turn-1",
              query: "客户预算有限怎么办？",
              top_n: 20,
              top_k: 10,
              response: answerPayloadWithCitations(5)
            }
          ]
        }
      ]
    })
  );
  installFetchStub();
  render(<App />);

  const citationList = await screen.findByLabelText("引用列表");
  expect(within(citationList).getByRole("button", { name: /引用 1/ })).toBeInTheDocument();
  expect(within(citationList).getByRole("button", { name: /引用 3/ })).toBeInTheDocument();
  expect(
    within(citationList).queryByRole("button", { name: /引用 4/ })
  ).not.toBeInTheDocument();
  expect(
    within(citationList).queryByRole("button", { name: /引用 5/ })
  ).not.toBeInTheDocument();

  await user.click(within(citationList).getByRole("button", { name: "显示更多" }));

  expect(within(citationList).getByRole("button", { name: /引用 4/ })).toBeInTheDocument();
  expect(within(citationList).getByRole("button", { name: /引用 5/ })).toBeInTheDocument();
  expect(within(citationList).getByRole("button", { name: "收起" })).toBeInTheDocument();

  await user.click(within(citationList).getByRole("button", { name: "收起" }));

  expect(
    within(citationList).queryByRole("button", { name: /引用 4/ })
  ).not.toBeInTheDocument();
  expect(within(citationList).getByRole("button", { name: "显示更多" })).toBeInTheDocument();
});

test("loads status and submits a question", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub();
  render(<App />);

  expect(await screen.findByText("xhbx_sales_chunks")).toBeInTheDocument();

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(
    await screen.findByText("先承接预算，再讨论缴费期和保障缺口。")
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /引用 1/ })).toBeInTheDocument();
  expect(screen.getByText("处理过程")).toBeInTheDocument();
  expect(screen.getByText("已完成问题理解")).toBeInTheDocument();
  expect(screen.getByText("已完成证据重排")).toBeInTheDocument();
  expect(requests).toContainEqual({
    url: "/api/answer/stream",
    body: { query: "客户说每年不能超过80万怎么办？", top_n: 20, top_k: 5 }
  });
});

test("submits the question when pressing Enter in the input", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub();
  render(<App />);

  const input = screen.getByLabelText("输入问题");
  await user.type(input, "客户说每年不能超过80万怎么办？");
  await user.keyboard("{Enter}");

  expect(
    await screen.findByText("先承接预算，再讨论缴费期和保障缺口。")
  ).toBeInTheDocument();
  expect(requests).toContainEqual({
    url: "/api/answer/stream",
    body: { query: "客户说每年不能超过80万怎么办？", top_n: 20, top_k: 5 }
  });
});

test("keeps a newline when pressing Shift Enter in the input", async () => {
  const user = userEvent.setup();
  const { fetcher } = installFetchStub();
  render(<App />);

  const input = screen.getByLabelText("输入问题");
  await user.type(input, "第一行");
  await user.keyboard("{Shift>}{Enter}{/Shift}");
  await user.type(input, "第二行");

  expect(input).toHaveValue("第一行\n第二行");
  await waitFor(() => {
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

test("streams answer text before final metadata arrives", async () => {
  const user = userEvent.setup();
  let answerResolve!: (response: Response) => void;
  const answerPromise = new Promise<Response>((resolve) => {
    answerResolve = resolve;
  });
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/status")) {
      return jsonResponse(statusPayload);
    }
    if (url.endsWith("/api/answer/stream")) {
      return answerPromise;
    }
    return jsonResponse({ detail: "not found" }, { status: 404 });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(screen.getByText("正在生成回答...")).toBeInTheDocument();

  answerResolve(answerStreamResponse());

  expect(
    await screen.findByText("先承接预算，再讨论缴费期和保障缺口。")
  ).toBeInTheDocument();
  expect(screen.getByText("处理过程")).toBeInTheDocument();
});

test("selects a citation and reveals the source file", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub();
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));
  await user.click(await screen.findByRole("button", { name: /引用 1/ }));

  expect(screen.getByText("data/案例A/第1节.track-0.txt")).toBeInTheDocument();
  expect(screen.getByText("客户说每年保费预算不能超过80万")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "在 Finder 中显示文件" }));

  expect(await screen.findByText("已在 Finder 中显示文件。")).toBeInTheDocument();
  expect(requests).toContainEqual({
    url: "/api/source/reveal",
    body: { source_path: "data/案例A/第1节.track-0.txt" }
  });
});

test("shows retrieval evidence used by the answer model", async () => {
  const user = userEvent.setup();
  installFetchStub();
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(await screen.findByText("检索证据")).toBeInTheDocument();
  expect(
    screen.getByRole("region", { name: "检索证据列表" })
  ).toBeInTheDocument();
  expect(screen.getByText("证据 1 · objection_handling")).toBeInTheDocument();
  expect(
    screen.getByText("客户担心预算，可以先承接预算，再对齐保障缺口。")
  ).toBeInTheDocument();
  expect(screen.getByText("案例A · 需求分析")).toBeInTheDocument();
  expect(screen.getByText("第2节.track-0.txt · L1")).toBeInTheDocument();
});

test("submits a bad case with retrieval context", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub();
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));
  await user.click(await screen.findByRole("button", { name: "不完整" }));
  await user.click(screen.getByLabelText("缺关键话术"));
  await user.type(
    screen.getByLabelText("哪里不对"),
    "当前回答没有讲清楚保障缺口。"
  );
  await user.type(
    screen.getByLabelText("正确回答应包含什么"),
    "应该命中保障缺口分析，而不是只命中销售动作。"
  );
  await user.type(screen.getByLabelText("相关案例/章节/文件名"), "案例A 第3节");
  await user.click(screen.getByLabelText("证据 1 应该用"));
  await user.click(screen.getByRole("button", { name: "保存反馈" }));

  expect(await screen.findByText("反馈已保存。")).toBeInTheDocument();
  expect(screen.queryByText("这个回答可用吗？")).not.toBeInTheDocument();
  expect(screen.queryByText("反馈这次回答")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("哪里不对")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "保存反馈" })).not.toBeInTheDocument();
  expect(requests).toContainEqual({
    url: "/api/bad-cases",
    body: {
      query: "客户说每年不能超过80万怎么办？",
      rewritten_query: "客户预算上限80万时如何回应",
      answer: "先承接预算，再讨论缴费期和保障缺口。",
      top_n: 20,
      top_k: 5,
      feedback_result: "incomplete",
      problem_tags: ["missing_talk_track"],
      problem_detail: "当前回答没有讲清楚保障缺口。",
      expected_answer: "应该命中保障缺口分析，而不是只命中销售动作。",
      reference_note: "案例A 第3节",
      evidence_feedback: [
        {
          chunk_id: "case-a-2",
          judgement: "should_use",
          label: "案例A · 需求分析",
          text_preview: "客户担心预算，可以先承接预算，再对齐保障缺口。"
        }
      ],
      issue_types: ["incomplete", "missing_talk_track"],
      expected_knowledge: "应该命中保障缺口分析，而不是只命中销售动作。",
      expected_source: "案例A 第3节",
      note: "当前回答没有讲清楚保障缺口。",
      citations: answerPayload.citations,
      retrieval_evidences: answerPayload.retrieval_evidences
    }
  });
});

test("records usable answer feedback without opening the bad case form", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub();
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));
  await user.click(await screen.findByRole("button", { name: "可用" }));

  expect(await screen.findByText("已记录可用反馈。")).toBeInTheDocument();
  expect(screen.queryByText("反馈这次回答")).not.toBeInTheDocument();
  expect(requests).toContainEqual({
    url: "/api/bad-cases",
    body: {
      query: "客户说每年不能超过80万怎么办？",
      rewritten_query: "客户预算上限80万时如何回应",
      answer: "先承接预算，再讨论缴费期和保障缺口。",
      top_n: 20,
      top_k: 5,
      feedback_result: "usable",
      problem_tags: [],
      problem_detail: "",
      expected_answer: "",
      reference_note: "",
      evidence_feedback: [],
      issue_types: ["usable"],
      expected_knowledge: "",
      expected_source: "",
      note: "",
      citations: answerPayload.citations,
      retrieval_evidences: answerPayload.retrieval_evidences
    }
  });
});

test("does not submit an empty question", async () => {
  const user = userEvent.setup();
  const { fetcher } = installFetchStub();
  render(<App />);

  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(screen.getByText("请输入问题后再发送。")).toBeInTheDocument();
  await waitFor(() => {
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

test("validates citation count before submit", async () => {
  const user = userEvent.setup();
  const { fetcher } = installFetchStub();
  render(<App />);

  await user.clear(screen.getByLabelText("召回数量"));
  await user.type(screen.getByLabelText("召回数量"), "1");
  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(screen.getByText("引用数量不能大于召回数量。")).toBeInTheDocument();
  await waitFor(() => {
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

test("disables clear while an answer is loading", async () => {
  const user = userEvent.setup();
  const answer = deferredResponse();
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/status")) {
      return jsonResponse(statusPayload);
    }
    if (url.endsWith("/api/answer/stream")) {
      return answer.promise;
    }
    return jsonResponse({ detail: "not found" }, { status: 404 });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(screen.getByRole("button", { name: "清空" })).toBeDisabled();

  answer.resolve(answerStreamResponse());
  expect(await screen.findByText("先承接预算，再讨论缴费期和保障缺口。")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "清空" })).toBeEnabled();
});
