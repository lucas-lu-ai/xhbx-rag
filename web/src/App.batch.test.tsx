import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "./App";
import {
  answerPayload,
  batchRunDetail,
  batchRunProgressOf,
  batchRunQuestionDetail,
  batchRunSummary,
  deferredResponse,
  installDownloadStub,
  installFetchStub,
  installStorageStub,
  jsonResponse,
  makeXlsxFile,
  readXlsxBlob,
  runRegisteredCleanups
} from "./test-utils";
import type { BatchRunDetail, BatchRunProgress, BatchRunSummary } from "./types";

beforeEach(() => {
  installStorageStub();
});

afterEach(() => {
  runRegisteredCleanups();
  vi.unstubAllGlobals();
});

async function openBatchCreateView(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "批量执行" }));
}

test("从侧栏批量执行按钮进入创建视图并解析粘贴内容", async () => {
  const user = userEvent.setup();
  installFetchStub();
  render(<App />);

  await openBatchCreateView(user);
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

test("解析上传的 xlsx 批量文件的第一张 sheet", async () => {
  const user = userEvent.setup();
  const file = await makeXlsxFile([
    ["问题", "答案", "标签"],
    ["客户说预算有限怎么办？", "", "预算"],
    ["保单整理有什么作用？", "人工答案", "整理"]
  ]);
  installFetchStub();
  render(<App />);

  await openBatchCreateView(user);
  await user.upload(screen.getByLabelText("上传批量文件"), file);

  expect(await screen.findByText("已解析 2 个问题")).toBeInTheDocument();
  expect(screen.getByText("客户说预算有限怎么办？")).toBeInTheDocument();
  expect(screen.getByText("保单整理有什么作用？")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "开始批量运行" })).toBeEnabled();
});

test("下载只含表头的 xlsx 批量模板", async () => {
  const user = userEvent.setup();
  const { blobs, restore } = installDownloadStub();
  installFetchStub();
  render(<App />);

  await openBatchCreateView(user);
  await user.click(screen.getByRole("button", { name: "下载 xlsx 模板" }));

  await waitFor(() => {
    expect(blobs).toHaveLength(1);
  });
  const rows = await readXlsxBlob(blobs[0]);
  expect(rows).toEqual([["问题", "答案"]]);
  restore();
});

test("粘贴内容变化后清空已解析结果", async () => {
  const user = userEvent.setup();
  installFetchStub();
  render(<App />);

  await openBatchCreateView(user);
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

test("开始批量运行创建服务端任务并切换到批量会话", async () => {
  const user = userEvent.setup();
  const summary = batchRunSummary({
    run_id: "run-9",
    title: "客户说每年不能超过80万怎么办？",
    status: "pending",
    source_label: "pasted",
    source_format: "pasted",
    question_total: 1,
    question_done: 0
  });
  const detail: BatchRunDetail = {
    ...summary,
    status: "completed",
    question_done: 1,
    questions: [batchRunQuestionDetail()]
  };
  let created = false;
  const { requests } = installFetchStub((url, init) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/batch-runs") && method === "POST") {
      created = true;
      return jsonResponse(summary, { status: 201 });
    }
    if (url.endsWith("/api/batch-runs") && method === "GET") {
      return jsonResponse({ runs: created ? [summary] : [] });
    }
    if (url.endsWith("/api/batch-runs/run-9")) {
      return jsonResponse(detail);
    }
    return null;
  });
  render(<App />);

  await openBatchCreateView(user);
  await user.type(
    screen.getByLabelText("批量问题内容"),
    "问题,答案\n客户说每年不能超过80万怎么办？,人工答案"
  );
  await user.click(screen.getByRole("button", { name: "解析内容" }));
  await user.click(screen.getByRole("button", { name: "开始批量运行" }));

  // 批量列表屏只显示问题与状态，答案在行详情屏。
  const qaPanel = screen.getByRole("main", { name: "RAG 问答" });
  expect(
    await within(qaPanel).findByRole("button", {
      name: /客户说每年不能超过80万怎么办？/
    })
  ).toBeInTheDocument();
  expect(requests).toContainEqual(
    expect.objectContaining({
      url: "/api/batch-runs",
      method: "POST",
      body: {
        title: "客户说每年不能超过80万怎么办？",
        source_label: "pasted",
        source_format: "pasted",
        headers: ["问题", "答案"],
        rows: [["客户说每年不能超过80万怎么办？", "人工答案"]],
        questions: [
          {
            row_index: 1,
            query: "客户说每年不能超过80万怎么办？",
            input_answer: "人工答案",
            top_n: 20,
            top_k: 5
          }
        ]
      }
    })
  );

  const sidebar = screen.getByRole("navigation", { name: "历史会话" });
  const batchButton = within(sidebar)
    .getAllByRole("button")
    .filter((item) => item.hasAttribute("aria-pressed"))
    .find((item) => {
      const text = item.textContent ?? "";
      return text.includes("批量") && text.includes("客户说每年不能超过80万怎么办？");
    });
  expect(batchButton).toHaveAttribute("aria-pressed", "true");
});

test("POST 进行中禁用开始批量运行按钮", async () => {
  const user = userEvent.setup();
  const createResponse = deferredResponse();
  const summary = batchRunSummary({
    run_id: "run-9",
    status: "pending",
    source_label: "pasted",
    source_format: "pasted",
    question_done: 0
  });
  const detail: BatchRunDetail = {
    ...summary,
    status: "completed",
    question_done: 1,
    questions: [batchRunQuestionDetail()]
  };
  installFetchStub((url, init) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/batch-runs") && method === "POST") {
      return createResponse.promise;
    }
    if (url.endsWith("/api/batch-runs/run-9")) {
      return jsonResponse(detail);
    }
    return null;
  });
  render(<App />);

  await openBatchCreateView(user);
  await user.type(
    screen.getByLabelText("批量问题内容"),
    "问题,答案\n客户说每年不能超过80万怎么办？,人工答案"
  );
  await user.click(screen.getByRole("button", { name: "解析内容" }));
  await user.click(screen.getByRole("button", { name: "开始批量运行" }));

  expect(screen.getByRole("button", { name: "开始批量运行" })).toBeDisabled();

  createResponse.resolve(jsonResponse(summary, { status: 201 }));

  const qaPanel = screen.getByRole("main", { name: "RAG 问答" });
  expect(
    await within(qaPanel).findByRole("button", {
      name: /客户说每年不能超过80万怎么办？/
    })
  ).toBeInTheDocument();
});

test("批量会话轮询进度直到终态并展示行状态", async () => {
  installStorageStub().setItem(
    "xhbx-rag.active-session.v1",
    JSON.stringify({ kind: "batch", id: "run-1" })
  );
  const pendingDetail = batchRunDetail({
    status: "pending",
    question_done: 0,
    questions: [batchRunQuestionDetail({ status: "pending", response: null })]
  });
  const runningDetail = batchRunDetail({
    status: "running",
    question_done: 0,
    questions: [batchRunQuestionDetail({ status: "running", response: null })]
  });
  const completedDetail = batchRunDetail({
    status: "completed",
    question_done: 1,
    questions: [batchRunQuestionDetail({ status: "succeeded" })]
  });
  const detailQueue = [pendingDetail, runningDetail, completedDetail];
  const progressQueue: BatchRunProgress[] = [
    batchRunProgressOf(runningDetail),
    batchRunProgressOf(completedDetail)
  ];
  installFetchStub((url, init) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/batch-runs") && method === "GET") {
      return jsonResponse({ runs: [batchRunSummary({ status: "pending" })] });
    }
    if (url.endsWith("/api/batch-runs/run-1/progress")) {
      const progress =
        progressQueue.length > 1 ? progressQueue.shift() : progressQueue[0];
      return jsonResponse(progress);
    }
    if (url.endsWith("/api/batch-runs/run-1")) {
      const detail = detailQueue.length > 1 ? detailQueue.shift() : detailQueue[0];
      return jsonResponse(detail);
    }
    return null;
  });
  render(<App batchPollIntervalMs={5} listPollIntervalMs={5} />);

  // 轮询到终态后列表屏行状态与批次统计更新。
  expect(await screen.findByText("完成 1")).toBeInTheDocument();
  expect(screen.getAllByText("已完成").length).toBeGreaterThan(0);
  expect(screen.getByText("总数 1")).toBeInTheDocument();
  expect(screen.getByText("失败 0")).toBeInTheDocument();

  // 点击行进入会话式详情屏可看到模型答案。
  const user = userEvent.setup();
  const qaPanel = screen.getByRole("main", { name: "RAG 问答" });
  await user.click(
    within(qaPanel).getByRole("button", {
      name: /客户说每年不能超过80万怎么办？/
    })
  );
  expect(
    await screen.findByText("先承接预算，再讨论缴费期和保障缺口。")
  ).toBeInTheDocument();
  // “人工答案”既是区块标签又是夹具内容，允许多处命中。
  expect(screen.getAllByText("人工答案").length).toBeGreaterThan(0);
  expect(screen.getByText("返回列表")).toBeInTheDocument();
});

test("失败行展示错误并支持重试", async () => {
  const user = userEvent.setup();
  installStorageStub().setItem(
    "xhbx-rag.active-session.v1",
    JSON.stringify({ kind: "batch", id: "run-1" })
  );
  const failedDetail = batchRunDetail({
    status: "completed",
    question_done: 0,
    question_failed: 1,
    questions: [
      batchRunQuestionDetail({
        status: "failed",
        response: null,
        error: "问答服务暂时不可用"
      })
    ]
  });
  const retriedDetail = batchRunDetail({
    status: "completed",
    question_done: 1,
    question_failed: 0,
    questions: [
      batchRunQuestionDetail({
        status: "succeeded",
        response: { ...answerPayload, answer: "重试后的模型答案" }
      })
    ]
  });
  let retried = false;
  const { requests } = installFetchStub((url, init) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/batch-runs") && method === "GET") {
      return jsonResponse({ runs: [batchRunSummary()] });
    }
    if (url.endsWith("/api/batch-runs/run-1/rows/1/retry") && method === "POST") {
      retried = true;
      return jsonResponse({ ok: true });
    }
    if (url.endsWith("/api/batch-runs/run-1")) {
      return jsonResponse(retried ? retriedDetail : failedDetail);
    }
    return null;
  });
  render(<App />);

  // 列表屏显示失败状态与重试入口，错误详情在行详情屏。
  expect(await screen.findByText("失败")).toBeInTheDocument();
  const qaPanel = screen.getByRole("main", { name: "RAG 问答" });
  await user.click(
    within(qaPanel).getByRole("button", {
      name: /客户说每年不能超过80万怎么办？/
    })
  );
  expect(await screen.findByText("问答服务暂时不可用")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "重试" }));

  expect(await screen.findByText("重试后的模型答案")).toBeInTheDocument();
  expect(requests).toContainEqual(
    expect.objectContaining({
      url: "/api/batch-runs/run-1/rows/1/retry",
      method: "POST"
    })
  );
});

test("中断的批量会话支持继续执行", async () => {
  const user = userEvent.setup();
  installStorageStub().setItem(
    "xhbx-rag.active-session.v1",
    JSON.stringify({ kind: "batch", id: "run-1" })
  );
  const interruptedDetail = batchRunDetail({
    status: "interrupted",
    question_done: 0,
    questions: [batchRunQuestionDetail({ status: "pending", response: null })]
  });
  const completedDetail = batchRunDetail({
    status: "completed",
    question_done: 1,
    questions: [batchRunQuestionDetail({ status: "succeeded" })]
  });
  let resumed = false;
  const { requests } = installFetchStub((url, init) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/batch-runs") && method === "GET") {
      return jsonResponse({
        runs: [batchRunSummary({ status: "interrupted" })]
      });
    }
    if (url.endsWith("/api/batch-runs/run-1/resume") && method === "POST") {
      resumed = true;
      return jsonResponse({ ok: true });
    }
    if (url.endsWith("/api/batch-runs/run-1/progress")) {
      return jsonResponse(
        batchRunProgressOf(resumed ? completedDetail : interruptedDetail)
      );
    }
    if (url.endsWith("/api/batch-runs/run-1")) {
      return jsonResponse(resumed ? completedDetail : interruptedDetail);
    }
    return null;
  });
  render(<App batchPollIntervalMs={5} listPollIntervalMs={5} />);

  expect(await screen.findByText("已中断")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "继续执行" }));

  expect(await screen.findByText("完成 1")).toBeInTheDocument();
  expect(requests).toContainEqual(
    expect.objectContaining({
      url: "/api/batch-runs/run-1/resume",
      method: "POST"
    })
  );
});

test("导出回填文件走 include_table 详情并复用回填纯函数", async () => {
  const user = userEvent.setup();
  installStorageStub().setItem(
    "xhbx-rag.active-session.v1",
    JSON.stringify({ kind: "batch", id: "run-1" })
  );
  const detail = batchRunDetail({
    source_label: "qa.csv",
    source_format: "csv"
  });
  const detailWithTable: BatchRunDetail = {
    ...detail,
    headers: ["问题", "答案", "标签"],
    rows: [["客户说每年不能超过80万怎么办？", "", "预算"]]
  };
  const { textParts, restore } = installDownloadStub();
  const { requests } = installFetchStub((url, init) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/batch-runs") && method === "GET") {
      return jsonResponse({ runs: [batchRunSummary()] });
    }
    if (url.endsWith("/api/batch-runs/run-1?include_table=true")) {
      return jsonResponse(detailWithTable);
    }
    if (url.endsWith("/api/batch-runs/run-1")) {
      return jsonResponse(detail);
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

  await user.click(screen.getByRole("button", { name: "下载回填文件" }));

  await waitFor(() => {
    expect(textParts.join("")).toContain("问题,答案,标签");
    expect(textParts.join("")).toContain(
      "客户说每年不能超过80万怎么办？,先承接预算，再讨论缴费期和保障缺口。,预算"
    );
  });
  expect(requests).toContainEqual(
    expect.objectContaining({
      url: "/api/batch-runs/run-1?include_table=true",
      method: "GET"
    })
  );
  restore();
});

test("导出 bad case JSONL 只包含非 usable 记录", async () => {
  const user = userEvent.setup();
  installStorageStub().setItem(
    "xhbx-rag.active-session.v1",
    JSON.stringify({ kind: "batch", id: "run-1" })
  );
  const badCaseRecord = {
    feedback_result: "incomplete",
    batch_source_label: "qa.csv",
    row_index: 1,
    input_answer: "人工答案",
    query: "客户说每年不能超过80万怎么办？"
  };
  const usableRecord = {
    feedback_result: "usable",
    batch_source_label: "qa.csv",
    row_index: 2,
    input_answer: "可用原答案"
  };
  const detail = batchRunDetail({
    question_total: 2,
    question_done: 2,
    questions: [
      batchRunQuestionDetail({ bad_case: badCaseRecord }),
      batchRunQuestionDetail({
        row_index: 2,
        query: "保单整理有什么作用？",
        bad_case: usableRecord
      })
    ]
  });
  const detailWithTable: BatchRunDetail = {
    ...detail,
    headers: ["问题", "答案"],
    rows: [
      ["客户说每年不能超过80万怎么办？", ""],
      ["保单整理有什么作用？", ""]
    ]
  };
  const { textParts, restore } = installDownloadStub();
  installFetchStub((url, init) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/batch-runs") && method === "GET") {
      return jsonResponse({ runs: [batchRunSummary()] });
    }
    if (url.endsWith("/api/batch-runs/run-1?include_table=true")) {
      return jsonResponse(detailWithTable);
    }
    if (url.endsWith("/api/batch-runs/run-1")) {
      return jsonResponse(detail);
    }
    return null;
  });
  render(<App />);

  expect(
    await screen.findByRole("button", { name: "下载 bad case JSONL" })
  ).toBeEnabled();

  await user.click(screen.getByRole("button", { name: "下载 bad case JSONL" }));

  await waitFor(() => {
    const jsonl = textParts.join("").trim();
    expect(jsonl).not.toBe("");
    const lines = jsonl.split("\n");
    expect(lines).toHaveLength(1);
    expect(JSON.parse(lines[0])).toMatchObject({
      feedback_result: "incomplete",
      batch_source_label: "qa.csv",
      row_index: 1,
      input_answer: "人工答案"
    });
  });
  restore();
});

test("批量行反馈只调批量单入口并本地更新 bad_case", async () => {
  const user = userEvent.setup();
  installStorageStub().setItem(
    "xhbx-rag.active-session.v1",
    JSON.stringify({ kind: "batch", id: "run-1" })
  );
  const detail = batchRunDetail();
  const { requests } = installFetchStub((url, init) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/batch-runs") && method === "GET") {
      return jsonResponse({ runs: [batchRunSummary()] });
    }
    if (
      url.endsWith("/api/batch-runs/run-1/rows/1/bad-case") &&
      method === "POST"
    ) {
      return jsonResponse({ ok: true, bad_case_id: "bad-case-7" });
    }
    if (url.endsWith("/api/batch-runs/run-1")) {
      return jsonResponse(detail);
    }
    return null;
  });
  render(<App />);

  // 反馈操作在行详情屏内完成：批量不再有回答级反馈，
  // 证据“不该用”带理由直接落地行级 bad case。
  const qaPanel = screen.getByRole("main", { name: "RAG 问答" });
  await user.click(
    await within(qaPanel).findByRole("button", {
      name: /客户说每年不能超过80万怎么办？/
    })
  );
  expect(
    await screen.findByText("先承接预算，再讨论缴费期和保障缺口。")
  ).toBeInTheDocument();
  expect(screen.queryByText("这个回答可用吗？")).not.toBeInTheDocument();

  await user.click(await screen.findByLabelText("证据 1 不该用"));
  await user.type(
    screen.getByLabelText("不可用理由"),
    "该证据与客户问题无关。"
  );
  await user.click(screen.getByRole("button", { name: "保存不可用反馈" }));

  expect(await screen.findByText("已记录不可用反馈。")).toBeInTheDocument();
  expect(requests).toContainEqual(
    expect.objectContaining({
      url: "/api/batch-runs/run-1/rows/1/bad-case",
      method: "POST",
      body: expect.objectContaining({
        query: "客户说每年不能超过80万怎么办？",
        feedback_result: "citation_issue",
        issue_types: ["citation_issue"],
        problem_detail: "该证据与客户问题无关。",
        input_answer: "人工答案",
        batch_source_label: "qa.csv"
      })
    })
  );
  expect(
    requests.filter((request) => request.url.endsWith("/api/bad-cases"))
  ).toHaveLength(0);
  // 本地已回写 bad_case：返回列表后行带“已反馈”徽标，导出按钮无需等待下一次轮询即可用。
  await user.click(screen.getByRole("button", { name: "返回列表" }));
  expect(screen.getByText("已反馈")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "下载 bad case JSONL" })).toBeEnabled();
});

test("批量行详情自动选中证据并联动右侧明细", async () => {
  const user = userEvent.setup();
  installStorageStub().setItem(
    "xhbx-rag.active-session.v1",
    JSON.stringify({ kind: "batch", id: "run-1" })
  );
  installFetchStub((url, init) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/batch-runs") && method === "GET") {
      return jsonResponse({ runs: [batchRunSummary()] });
    }
    if (url.endsWith("/api/batch-runs/run-1")) {
      return jsonResponse(batchRunDetail());
    }
    return null;
  });
  render(<App />);

  const qaPanel = screen.getByRole("main", { name: "RAG 问答" });
  await user.click(
    await within(qaPanel).findByRole("button", {
      name: /客户说每年不能超过80万怎么办？/
    })
  );
  expect(
    await screen.findByText("先承接预算，再讨论缴费期和保障缺口。")
  ).toBeInTheDocument();

  // 进入详情屏自动选中第一条证据，右侧明细展示正文与来源摘录。
  const detailPane = screen.getByRole("complementary", {
    name: "索引和溯源"
  });
  expect(
    await within(detailPane).findByText("证据 1 · 异议处理")
  ).toBeInTheDocument();
  expect(
    within(detailPane).getByText("data/案例A/第2节.track-0.txt")
  ).toBeInTheDocument();

  // 展开紧凑证据列表，选中行处于按下状态。
  await user.click(screen.getByRole("button", { name: /检索证据/ }));
  const evidenceList = await screen.findByRole("region", {
    name: "检索证据列表"
  });
  const rows = within(evidenceList).getAllByRole("button");
  expect(rows[0]).toHaveAttribute("aria-pressed", "true");
});
