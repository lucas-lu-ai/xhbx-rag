import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "./App";
import {
  answerPayload,
  answerPayloadWithEvidences,
  answerStreamResponse,
  deferredResponse,
  installFetchStub,
  jsonResponse,
  installStorageStub,
  runRegisteredCleanups,
  sseResponse
} from "./test-utils";

beforeEach(() => {
  installStorageStub();
});

afterEach(() => {
  runRegisteredCleanups();
  vi.unstubAllGlobals();
});

test("uses default retrieval and citation limits", async () => {
  installFetchStub();

  render(<App />);

  expect(await screen.findByLabelText("召回数量")).toHaveValue(20);
  expect(screen.getByLabelText("引用数量")).toHaveValue(5);
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

test("marks answer-cited evidence cards with a badge", async () => {
  const user = userEvent.setup();
  localStorage.setItem(
    "xhbx-rag.chat-sessions.v1",
    JSON.stringify({
      version: 1,
      active_session_id: "session-1",
      sessions: [
        {
          id: "session-1",
          title: "多证据回答",
          created_at: "2026-07-01T08:00:00.000Z",
          updated_at: "2026-07-01T08:01:00.000Z",
          turns: [
            {
              id: "turn-1",
              query: "客户预算有限怎么办？",
              top_n: 20,
              top_k: 10,
              response: answerPayloadWithEvidences(2, [2])
            }
          ]
        }
      ]
    })
  );
  installFetchStub();
  render(<App />);

  await user.click(
    await screen.findByRole("button", { name: /检索证据/ })
  );
  const evidenceList = await screen.findByRole("region", {
    name: "检索证据列表"
  });
  const rows = within(evidenceList).getAllByRole("button");
  expect(rows).toHaveLength(2);
  expect(within(rows[0]).queryByText("答案引用")).not.toBeInTheDocument();
  expect(within(rows[1]).getByText("答案引用")).toBeInTheDocument();
});

test("loads status and submits a question", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub();
  render(<App />);

  expect(await screen.findByText("案例知识库")).toBeInTheDocument();

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(
    await screen.findByText("先承接预算，再讨论缴费期和保障缺口。")
  ).toBeInTheDocument();
  // 回答完成后自动选中第一条证据，右侧明细直接展示来源引用。
  expect(
    await screen.findByRole("button", { name: "第2节.track-0.txt · L1" })
  ).toBeInTheDocument();
  expect(screen.getByText("处理过程")).toBeInTheDocument();
  // 回答完成后处理过程自动折叠，展开后可见步骤明细。
  await user.click(screen.getByRole("button", { name: /处理过程/ }));
  expect(screen.getByText("已完成问题理解")).toBeInTheDocument();
  expect(screen.getByText("已完成证据重排")).toBeInTheDocument();
  expect(requests).toContainEqual(
    expect.objectContaining({
      url: "/api/answer/stream",
      body: { query: "客户说每年不能超过80万怎么办？", top_n: 20, top_k: 5 }
    })
  );
});

test("submits selected collections from the status dropdown", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub((url) => {
    if (url.endsWith("/api/status")) {
      return jsonResponse({
        ok: true,
        data_dir: "data",
        milvus_mode: "lite",
        milvus_target: ".local/milvus/xhbx_rag.db",
        milvus_lite_path: ".local/milvus/xhbx_rag.db",
        milvus_collection: "xhbx_sales_chunks",
        milvus_course_collection: "xhbx_course_chunks",
        milvus_collections: ["xhbx_sales_chunks", "xhbx_course_chunks"],
        batch_concurrency: 1,
        config: { API_KEY: true },
        errors: []
      });
    }
    return null;
  });
  render(<App />);

  await user.click(await screen.findByRole("button", { name: "选择 Collection" }));
  expect(screen.getAllByText("案例知识库").length).toBeGreaterThan(0);
  expect(screen.getByText("课程知识库")).toBeInTheDocument();
  await user.click(screen.getByLabelText("案例知识库"));
  await user.type(screen.getByLabelText("输入问题"), "促成课程怎么讲？");
  await user.click(screen.getByRole("button", { name: "发送" }));

  await waitFor(() => {
    expect(requests).toContainEqual(
      expect.objectContaining({
        url: "/api/answer/stream",
        body: expect.objectContaining({
          query: "促成课程怎么讲？",
          top_n: 20,
          top_k: 5,
          collections: ["xhbx_course_chunks"]
        })
      })
    );
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
  expect(requests).toContainEqual(
    expect.objectContaining({
      url: "/api/answer/stream",
      body: { query: "客户说每年不能超过80万怎么办？", top_n: 20, top_k: 5 }
    })
  );
});

test("keeps a newline when pressing Shift Enter in the input", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub();
  render(<App />);

  const input = screen.getByLabelText("输入问题");
  await user.type(input, "第一行");
  await user.keyboard("{Shift>}{Enter}{/Shift}");
  await user.type(input, "第二行");

  expect(input).toHaveValue("第一行\n第二行");
  await waitFor(() => {
    expect(
      requests.filter((request) => request.url.endsWith("/api/answer/stream"))
    ).toHaveLength(0);
  });
});

test("streams answer text before final metadata arrives", async () => {
  const user = userEvent.setup();
  const answer = deferredResponse();
  installFetchStub((url) => {
    if (url.endsWith("/api/answer/stream")) {
      return answer.promise;
    }
    return null;
  });
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(screen.getByText("正在生成回答...")).toBeInTheDocument();

  answer.resolve(answerStreamResponse());

  expect(
    await screen.findByText("先承接预算，再讨论缴费期和保障缺口。")
  ).toBeInTheDocument();
  expect(screen.getByText("处理过程")).toBeInTheDocument();
});

test("streams thinking deltas and collapses them after the answer arrives", async () => {
  const user = userEvent.setup();
  installFetchStub((url) => {
    if (url.endsWith("/api/answer/stream")) {
      return sseResponse([
        {
          event: "thinking_delta",
          data: { type: "thinking_delta", text: "先分析预算约束，" }
        },
        {
          event: "thinking_delta",
          data: { type: "thinking_delta", text: "再匹配可行方案。" }
        },
        {
          event: "answer_delta",
          data: { type: "answer_delta", text: "先承接预算，再讨论缴费期和保障缺口。" }
        },
        {
          event: "final",
          data: {
            type: "final",
            response: {
              ...answerPayload,
              reasoning: "先分析预算约束，再匹配可行方案。"
            }
          }
        }
      ]);
    }
    return null;
  });
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));

  const toggle = await screen.findByRole("button", { name: /思考过程/ });
  // 回答完成后思考块自动折叠，点击可重新展开完整推理。
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  await user.click(toggle);
  expect(
    screen.getByText("先分析预算约束，再匹配可行方案。")
  ).toBeInTheDocument();
});

test("selects an evidence source and reveals the source file", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub();
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));
  // 回答完成自动选中第一条证据，明细内第一条引用默认选中并展示摘录。
  const sourceButton = await screen.findByRole("button", {
    name: "第2节.track-0.txt · L1"
  });

  expect(sourceButton).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("data/案例A/第2节.track-0.txt")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "在 Finder 中显示文件" }));

  expect(await screen.findByText("已在 Finder 中显示文件。")).toBeInTheDocument();
  expect(requests).toContainEqual(
    expect.objectContaining({
      url: "/api/source/reveal",
      body: { source_path: "data/案例A/第2节.track-0.txt" }
    })
  );
});

test("shows retrieval evidence used by the answer model", async () => {
  const user = userEvent.setup();
  installFetchStub();
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(await screen.findByText("检索证据")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /检索证据/ }));
  const evidenceList = screen.getByRole("region", { name: "检索证据列表" });
  // 紧凑行：名称、类型、答案引用徽标与单行预览。
  expect(within(evidenceList).getByText("案例A · 需求分析")).toBeInTheDocument();
  expect(within(evidenceList).getByText("异议处理")).toBeInTheDocument();
  expect(within(evidenceList).getByText("答案引用")).toBeInTheDocument();
  expect(
    within(evidenceList).getByText(
      "客户担心预算，可以先承接预算，再对齐保障缺口。"
    )
  ).toBeInTheDocument();
  // 自动选中第一条证据后右侧明细展示完整信息与来源引用。
  const detailPane = screen.getByRole("complementary", {
    name: "索引和溯源"
  });
  expect(
    within(detailPane).getByText("证据 1 · 异议处理")
  ).toBeInTheDocument();
  expect(
    within(detailPane).getByRole("button", { name: "第2节.track-0.txt · L1" })
  ).toBeInTheDocument();
});

test("marks an evidence as useful and saves a usable bad case", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub();
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));
  await user.click(await screen.findByLabelText("证据 1 应该用"));

  expect(await screen.findByText("已记录可用反馈。")).toBeInTheDocument();
  expect(screen.getByLabelText("证据 1 应该用")).toBeChecked();
  expect(requests).toContainEqual(
    expect.objectContaining({
      url: "/api/bad-cases",
      body: expect.objectContaining({
        query: "客户说每年不能超过80万怎么办？",
        feedback_result: "usable",
        issue_types: ["usable"],
        evidence_feedback: [
          {
            chunk_id: "case-a-2",
            judgement: "should_use",
            label: "案例A · 需求分析",
            text_preview: "客户担心预算，可以先承接预算，再对齐保障缺口。"
          }
        ]
      })
    })
  );
});

test("marks an evidence as not useful with a reason and saves a bad case", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub();
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));
  await user.click(await screen.findByLabelText("证据 1 不该用"));
  await user.type(
    screen.getByLabelText("不可用理由"),
    "该证据与客户问题无关。"
  );
  await user.click(screen.getByRole("button", { name: "保存不可用反馈" }));

  expect(await screen.findByText("已记录不可用反馈。")).toBeInTheDocument();
  expect(screen.getByLabelText("证据 1 不该用")).toBeChecked();
  expect(requests).toContainEqual(
    expect.objectContaining({
      url: "/api/bad-cases",
      body: expect.objectContaining({
        query: "客户说每年不能超过80万怎么办？",
        feedback_result: "citation_issue",
        issue_types: ["citation_issue"],
        problem_detail: "该证据与客户问题无关。",
        note: "该证据与客户问题无关。",
        evidence_feedback: [
          {
            chunk_id: "case-a-2",
            judgement: "should_not_use",
            label: "案例A · 需求分析",
            text_preview: "客户担心预算，可以先承接预算，再对齐保障缺口。",
            reason: "该证据与客户问题无关。"
          }
        ]
      })
    })
  );
});

test("does not submit an empty question", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub();
  render(<App />);

  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(screen.getByText("请输入问题后再发送。")).toBeInTheDocument();
  expect(
    requests.filter((request) => request.url.endsWith("/api/answer/stream"))
  ).toHaveLength(0);
});

test("validates citation count before submit", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub();
  render(<App />);

  await user.clear(screen.getByLabelText("召回数量"));
  await user.type(screen.getByLabelText("召回数量"), "1");
  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(screen.getByText("引用数量不能大于召回数量。")).toBeInTheDocument();
  expect(
    requests.filter((request) => request.url.endsWith("/api/answer/stream"))
  ).toHaveLength(0);
});

test("disables clear while an answer is loading", async () => {
  const user = userEvent.setup();
  const answer = deferredResponse();
  installFetchStub((url) => {
    if (url.endsWith("/api/answer/stream")) {
      return answer.promise;
    }
    return null;
  });
  render(<App />);

  await user.type(screen.getByLabelText("输入问题"), "客户说每年不能超过80万怎么办？");
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(screen.getByRole("button", { name: "清空" })).toBeDisabled();

  answer.resolve(answerStreamResponse());
  expect(await screen.findByText("先承接预算，再讨论缴费期和保障缺口。")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "清空" })).toBeEnabled();
});

test("removes the mode switch from the main panel header", async () => {
  installFetchStub();
  render(<App />);

  expect(await screen.findByLabelText("输入问题")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "单问" })).not.toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "工作模式" })).not.toBeInTheDocument();
});
