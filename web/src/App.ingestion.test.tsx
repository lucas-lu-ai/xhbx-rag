import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "./App";
import {
  deleteIngestionJob,
  retryIngestionJob,
  startIngestionJob
} from "./api";
import {
  ingestionDetail,
  ingestionDraftPayload,
  ingestionSummary,
  deferredResponse,
  installFetchStub,
  installIngestionApiStub,
  installStorageStub,
  jsonResponse,
  runRegisteredCleanups
} from "./test-utils";

beforeEach(() => {
  installStorageStub();
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  runRegisteredCleanups();
  vi.unstubAllGlobals();
});

test("打开入库工作台后上传、预检并确认 draft", async () => {
  const user = userEvent.setup();
  const { requests } = installIngestionApiStub({
    draft: ingestionDraftPayload(),
    jobs: []
  });
  render(<App />);

  await user.click(screen.getByRole("button", { name: "文档入库" }));
  expect(window.location.search).toBe("?view=ingestion");
  await user.click(screen.getByRole("radio", { name: "案例知识库" }));
  await user.upload(
    screen.getByLabelText("上传文档或 ZIP"),
    new File(["content"], "优秀案例.zip", { type: "application/zip" })
  );

  expect(await screen.findByText("识别到 3 个案例")).toBeInTheDocument();
  expect(screen.getByText("王女士年金险案例")).toBeInTheDocument();
  expect(screen.getByText("共 6 份文档 · 忽略 1 项")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "确认并开始" }));
  expect(await screen.findByText("排队中")).toBeInTheDocument();
  expect(requests).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        url: "/api/ingestion-jobs",
        method: "POST",
        body: expect.any(FormData)
      }),
      expect.objectContaining({
        url: "/api/ingestion-jobs/job-1/start",
        method: "POST"
      })
    ])
  );
});

test("失败任务说明未写入并可从头重试", async () => {
  const user = userEvent.setup();
  installIngestionApiStub({
    jobs: [ingestionSummary({ status: "failed" })],
    detail: ingestionDetail({
      status: "failed",
      current_stage: "parsing",
      error_code: "parse_failed",
      error_detail: "案例解析失败",
      item_done: 0,
      chunk_total: 0
    })
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);

  expect(await screen.findByText("任务未写入知识库")).toBeInTheDocument();
  expect(screen.getAllByText("案例解析失败").length).toBeGreaterThan(0);
  await user.click(screen.getByRole("button", { name: "从头重试" }));
  expect(await screen.findByText("排队中")).toBeInTheDocument();
});

test("回滚中明确提示并禁用重试和删除", async () => {
  installIngestionApiStub({
    jobs: [ingestionSummary({ status: "rolling_back" })],
    detail: ingestionDetail({ status: "rolling_back", current_stage: "indexing" })
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);

  expect(
    await screen.findByText("正在恢复知识库，请勿重试或删除")
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "从头重试" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "删除任务" })).toBeDisabled();
});

test.each(["queued", "running"] as const)(
  "%s 状态不提供开始或重试且禁止删除",
  async (status) => {
    installIngestionApiStub({
      jobs: [ingestionSummary({ status })],
      detail: ingestionDetail({ status, current_stage: status === "queued" ? "uploaded" : "parsing" })
    });
    window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
    render(<App />);

    await screen.findByRole("heading", { name: "优秀案例.zip" });
    expect(screen.queryByRole("button", { name: "确认并开始" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "从头重试" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除任务" })).toBeDisabled();
  }
);

test("删除确认可用 Escape 取消，确认后回退新建页", async () => {
  const user = userEvent.setup();
  const detail = ingestionDetail();
  const { requests } = installIngestionApiStub({
    jobs: [ingestionSummary()],
    detail
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);

  const deleteButton = await screen.findByRole("button", { name: "删除任务" });
  await user.click(deleteButton);
  const dialog = screen.getByRole("dialog", { name: "确认删除任务" });
  expect(dialog.tagName).toBe("DIALOG");
  expect(within(dialog).getByText(/不会删除已经成功入库的知识/)).toBeInTheDocument();
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "确认删除任务" })).not.toBeInTheDocument();
  await waitFor(() => expect(deleteButton).toHaveFocus());

  await user.click(screen.getByRole("button", { name: "删除任务" }));
  await user.click(
    within(screen.getByRole("dialog", { name: "确认删除任务" })).getByRole(
      "button",
      { name: "确认删除" }
    )
  );

  await waitFor(() => expect(window.location.search).toBe("?view=ingestion"));
  expect(screen.getByText("创建入库任务")).toBeInTheDocument();
  expect(requests).toContainEqual(
    expect.objectContaining({
      url: "/api/ingestion-jobs/job-1",
      method: "DELETE"
    })
  );
});

test("详情展示语义时间线但不渲染事件 payload", async () => {
  installIngestionApiStub({ jobs: [ingestionSummary()], detail: ingestionDetail() });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);

  const timeline = await screen.findByRole("list", { name: "任务时间线" });
  expect(within(timeline).getByText("任务开始处理")).toBeInTheDocument();
  expect(timeline.querySelector("time")).toHaveAttribute(
    "dateTime",
    "2026-07-10T08:01:00+00:00"
  );
  expect(screen.queryByText("/private/secret")).not.toBeInTheDocument();
  expect(screen.queryByText("do-not-render")).not.toBeInTheDocument();
});

test("切换任务后忽略前一个任务迟到的开始响应", async () => {
  const user = userEvent.setup();
  const draft = ingestionDraftPayload();
  const courseDetail = ingestionDetail({
    job_id: "job-2",
    source_name: "课程资料.pdf",
    source_kind: "file",
    target: "course",
    item_total: 1,
    document_total: 1
  });
  const startResponse = deferredResponse();
  installFetchStub((url, init) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/ingestion-jobs") && method === "GET") {
      return jsonResponse({ jobs: [ingestionSummary({ ...courseDetail }), ingestionSummary({ ...draft })] });
    }
    if (url.endsWith("/api/ingestion-jobs/job-1/start") && method === "POST") {
      return startResponse.promise;
    }
    if (url.endsWith("/api/ingestion-jobs/job-1") && method === "GET") {
      return jsonResponse(draft);
    }
    if (url.endsWith("/api/ingestion-jobs/job-2") && method === "GET") {
      return jsonResponse(courseDetail);
    }
    return null;
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);

  await user.click(await screen.findByRole("button", { name: "确认并开始" }));
  await user.click(screen.getByRole("button", { name: /课程资料\.pdf/ }));
  expect(await screen.findByRole("heading", { name: "课程资料.pdf" })).toBeInTheDocument();

  await act(async () => {
    startResponse.resolve(jsonResponse({ ok: true, job_id: "job-1", status: "queued" }));
  });

  expect(window.location.search).toBe("?view=ingestion&job=job-2");
  expect(screen.getByRole("heading", { name: "课程资料.pdf" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "优秀案例.zip" })).not.toBeInTheDocument();
});

test("详情刷新瞬时失败时保留旧内容并由后续轮询恢复", async () => {
  const running = ingestionDetail({
    status: "running",
    current_stage: "parsing",
    item_done: 1,
    updated_at: "2026-07-10T08:05:00+00:00"
  });
  const recovered = ingestionDetail({
    status: "running",
    current_stage: "chunking",
    item_done: 2,
    updated_at: "2026-07-10T08:07:00+00:00"
  });
  installIngestionApiStub({
    jobs: [ingestionSummary({ status: "running", current_stage: "parsing", item_done: 1 })],
    details: { "job-1": running },
    responses: {
      detail: {
        "job-1": [
          jsonResponse(running),
          jsonResponse({ detail: "详情暂时不可用" }, { status: 503 }),
          jsonResponse(recovered)
        ]
      },
      progress: {
        "job-1": [
          jsonResponse({
            job_id: "job-1",
            status: "running",
            current_stage: "parsing",
            attempt_no: 1,
            item_total: 3,
            item_done: 1,
            document_total: 6,
            chunk_total: 8,
            warning_count: 0,
            active_item_index: 2,
            message: "正在解析",
            updated_at: "2026-07-10T08:06:00+00:00"
          }),
          jsonResponse({
            job_id: "job-1",
            status: "running",
            current_stage: "chunking",
            attempt_no: 1,
            item_total: 3,
            item_done: 2,
            document_total: 6,
            chunk_total: 16,
            warning_count: 0,
            active_item_index: 3,
            message: "正在切分",
            updated_at: "2026-07-10T08:07:00+00:00"
          })
        ]
      }
    }
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App ingestionPollIntervalMs={40} />);

  expect(await screen.findByRole("heading", { name: "优秀案例.zip" })).toBeInTheDocument();
  expect(await screen.findByText("详情暂时不可用")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "优秀案例.zip" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重新加载任务详情" })).toBeInTheDocument();
  expect(await screen.findByText("2/3 项", {}, { timeout: 1000 })).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText("详情暂时不可用")).not.toBeInTheDocument());
});

test("开始接口 500 后按服务端 queued 状态对账", async () => {
  const user = userEvent.setup();
  const startResponse = deferredResponse();
  const draft = ingestionDraftPayload();
  const stub = installIngestionApiStub({
    jobs: [ingestionSummary({ ...draft })],
    details: { "job-1": draft },
    responses: { start: { "job-1": [startResponse.promise] } }
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);

  await user.click(await screen.findByRole("button", { name: "确认并开始" }));
  stub.setDetail({ ...draft, status: "queued" });
  await act(async () => {
    startResponse.resolve(jsonResponse({ detail: "启动请求超时" }, { status: 500 }));
  });

  expect(await screen.findByText("排队中")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "确认并开始" })).not.toBeInTheDocument();
});

test("动作失败且对账失败时保留旧详情并提供非阻断重载", async () => {
  const user = userEvent.setup();
  const draft = ingestionDraftPayload();
  installIngestionApiStub({
    jobs: [ingestionSummary({ ...draft })],
    details: { "job-1": draft },
    responses: {
      list: [
        jsonResponse({ jobs: [ingestionSummary({ ...draft })] }),
        jsonResponse({ detail: "列表对账失败" }, { status: 503 })
      ],
      detail: {
        "job-1": [
          jsonResponse(draft),
          jsonResponse({ detail: "详情对账失败" }, { status: 503 })
        ]
      },
      start: {
        "job-1": [jsonResponse({ detail: "启动请求失败" }, { status: 500 })]
      }
    }
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);

  await user.click(await screen.findByRole("button", { name: "确认并开始" }));
  expect(await screen.findByText("启动请求失败")).toBeInTheDocument();
  expect(screen.getByText("详情对账失败")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "优秀案例.zip" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "确认并开始" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "重新加载任务详情" })).toBeInTheDocument();
});

test("任务 A 动作进行中不禁用任务 B，A 的迟到失败不污染 B", async () => {
  const user = userEvent.setup();
  const draft = ingestionDraftPayload();
  const courseDetail = ingestionDetail({
    job_id: "job-2",
    source_name: "课程资料.pdf",
    source_kind: "file",
    target: "course"
  });
  const startResponse = deferredResponse();
  installIngestionApiStub({
    jobs: [ingestionSummary({ ...courseDetail }), ingestionSummary({ ...draft })],
    details: { "job-1": draft, "job-2": courseDetail },
    responses: { start: { "job-1": [startResponse.promise] } }
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);

  await user.click(await screen.findByRole("button", { name: "确认并开始" }));
  await user.click(screen.getByRole("button", { name: /课程资料\.pdf/ }));
  expect(await screen.findByRole("heading", { name: "课程资料.pdf" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "删除任务" })).toBeEnabled();

  await act(async () => {
    startResponse.resolve(jsonResponse({ detail: "任务 A 启动失败" }, { status: 500 }));
  });
  expect(screen.getByRole("heading", { name: "课程资料.pdf" })).toBeInTheDocument();
  expect(screen.queryByText("任务 A 启动失败")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "删除任务" })).toBeEnabled();
});

test("DELETE 成功后列表刷新失败仍关闭对话框并保持已删除", async () => {
  const user = userEvent.setup();
  const succeeded = ingestionDetail();
  installIngestionApiStub({
    jobs: [ingestionSummary()],
    details: { "job-1": succeeded },
    responses: {
      list: [
        jsonResponse({ jobs: [ingestionSummary()] }),
        jsonResponse({ detail: "列表暂时不可用" }, { status: 503 })
      ]
    }
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);

  await user.click(await screen.findByRole("button", { name: "删除任务" }));
  await user.click(screen.getByRole("button", { name: "确认删除" }));

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(window.location.search).toBe("?view=ingestion");
  expect(screen.queryByRole("button", { name: /优秀案例\.zip/ })).not.toBeInTheDocument();
  expect(screen.queryByText("删除任务失败，请稍后重试")).not.toBeInTheDocument();
});

test("删除任务 A 期间切到 B，完成后不清空或跳转 B", async () => {
  const user = userEvent.setup();
  const detailA = ingestionDetail();
  const detailB = ingestionDetail({ job_id: "job-2", source_name: "课程资料.pdf", target: "course" });
  const deleteResponse = deferredResponse();
  const reconcileList = deferredResponse();
  const stub = installIngestionApiStub({
    jobs: [ingestionSummary({ ...detailB }), ingestionSummary({ ...detailA })],
    details: { "job-1": detailA, "job-2": detailB },
    responses: {
      list: [
        jsonResponse({ jobs: [ingestionSummary({ ...detailB }), ingestionSummary({ ...detailA })] }),
        reconcileList.promise
      ],
      delete: { "job-1": [deleteResponse.promise] }
    }
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);

  await user.click(await screen.findByRole("button", { name: "删除任务" }));
  await user.click(screen.getByRole("button", { name: "确认删除" }));
  await user.click(screen.getByRole("button", { name: /课程资料\.pdf/ }));
  expect(await screen.findByRole("heading", { name: "课程资料.pdf" })).toBeInTheDocument();

  stub.removeDetail("job-1");
  await act(async () => {
    deleteResponse.resolve(jsonResponse({ ok: true, job_id: "job-1", status: "deleted" }));
    reconcileList.resolve(jsonResponse({ jobs: [ingestionSummary({ ...detailB })] }));
  });

  expect(window.location.search).toBe("?view=ingestion&job=job-2");
  expect(screen.getByRole("heading", { name: "课程资料.pdf" })).toBeInTheDocument();
});

test("删除后的乐观列表不被迟到的旧列表覆盖", async () => {
  const user = userEvent.setup();
  const oldList = deferredResponse();
  installIngestionApiStub({
    jobs: [ingestionSummary()],
    details: { "job-1": ingestionDetail() },
    responses: {
      list: [
        jsonResponse({ jobs: [ingestionSummary()] }),
        oldList.promise,
        jsonResponse({ jobs: [] })
      ]
    }
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);

  await screen.findByRole("button", { name: /优秀案例\.zip/ });
  await user.click(screen.getByRole("button", { name: "刷新任务列表" }));
  await user.click(screen.getByRole("button", { name: "删除任务" }));
  await user.click(screen.getByRole("button", { name: "确认删除" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

  await act(async () => {
    oldList.resolve(jsonResponse({ jobs: [ingestionSummary()] }));
  });
  expect(screen.queryByRole("button", { name: /优秀案例\.zip/ })).not.toBeInTheDocument();
});

test("轮询进度更新后的列表不被迟到的旧列表覆盖", async () => {
  const user = userEvent.setup();
  const running = ingestionDetail({ status: "running", current_stage: "indexing", item_done: 2 });
  const succeeded = ingestionDetail({ status: "succeeded", current_stage: "completed" });
  const oldList = deferredResponse();
  const progress = deferredResponse();
  const stub = installIngestionApiStub({
    jobs: [ingestionSummary({ status: "running", current_stage: "indexing", item_done: 2 })],
    details: { "job-1": running },
    responses: {
      list: [
        jsonResponse({ jobs: [ingestionSummary({ status: "running", current_stage: "indexing" })] }),
        oldList.promise
      ],
      progress: { "job-1": [progress.promise] }
    }
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);
  await screen.findByRole("heading", { name: "优秀案例.zip" });
  await user.click(screen.getByRole("button", { name: "刷新任务列表" }));

  stub.setDetail(succeeded);
  await act(async () => {
    progress.resolve(jsonResponse({
      job_id: "job-1",
      status: "succeeded",
      current_stage: "completed",
      attempt_no: 1,
      item_total: 3,
      item_done: 3,
      document_total: 6,
      chunk_total: 24,
      warning_count: 0,
      active_item_index: null,
      message: "入库完成",
      updated_at: "2026-07-10T08:08:00+00:00"
    }));
  });
  expect(await screen.findByRole("button", { name: /优秀案例\.zip.*已完成/ })).toBeInTheDocument();

  await act(async () => {
    oldList.resolve(jsonResponse({
      jobs: [ingestionSummary({ status: "running", current_stage: "indexing", item_done: 2 })]
    }));
  });
  expect(screen.getByRole("button", { name: /优秀案例\.zip.*已完成/ })).toBeInTheDocument();
});

test("重试接口 500 后按服务端 queued 状态对账", async () => {
  const user = userEvent.setup();
  const failed = ingestionDetail({ status: "failed", error_detail: "解析失败" });
  const retryResponse = deferredResponse();
  const stub = installIngestionApiStub({
    jobs: [ingestionSummary({ status: "failed" })],
    details: { "job-1": failed },
    responses: { retry: { "job-1": [retryResponse.promise] } }
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "从头重试" }));
  stub.setDetail({ ...failed, status: "queued", error_code: null, error_detail: null });
  await act(async () => {
    retryResponse.resolve(jsonResponse({ detail: "重试请求超时" }, { status: 500 }));
  });
  expect(await screen.findByText("排队中")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "从头重试" })).not.toBeInTheDocument();
});

test("删除接口 500 后按服务端 deleting 状态对账并关闭确认", async () => {
  const user = userEvent.setup();
  const succeeded = ingestionDetail();
  const deleteResponse = deferredResponse();
  const stub = installIngestionApiStub({
    jobs: [ingestionSummary()],
    details: { "job-1": succeeded },
    responses: { delete: { "job-1": [deleteResponse.promise] } }
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "删除任务" }));
  await user.click(screen.getByRole("button", { name: "确认删除" }));
  stub.setDetail({ ...succeeded, status: "deleting" });
  await act(async () => {
    deleteResponse.resolve(jsonResponse({ detail: "删除请求超时" }, { status: 500 }));
  });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(await screen.findByText("删除中")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "删除任务" })).toBeDisabled();
});

test.each(["failed", "rolling_back"] as const)(
  "%s 即使有 warning 也不声称核心知识已入库",
  async (status) => {
    installIngestionApiStub({
      jobs: [ingestionSummary({ status, warning_count: 2 })],
      detail: ingestionDetail({ status, warning_count: 2, error_detail: "处理失败" })
    });
    window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
    render(<App />);

    await screen.findByRole("heading", { name: "优秀案例.zip" });
    expect(screen.queryByText(/核心知识已完成入库/)).not.toBeInTheDocument();
  }
);

test("支持 showModal 时使用原生模态对话框", async () => {
  const user = userEvent.setup();
  const original = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, "showModal");
  const showModal = vi.fn(function (this: HTMLDialogElement) {
    this.open = true;
  });
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
    configurable: true,
    value: showModal
  });
  try {
    installIngestionApiStub({ jobs: [ingestionSummary()], detail: ingestionDetail() });
    window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "删除任务" }));
    expect(showModal).toHaveBeenCalledTimes(1);
  } finally {
    if (original) Object.defineProperty(HTMLDialogElement.prototype, "showModal", original);
    else Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
      configurable: true,
      value: undefined
    });
  }
});

test("dialog fallback 将 Tab 焦点限制在取消与确认按钮之间", async () => {
  const user = userEvent.setup();
  const original = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, "showModal");
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
    configurable: true,
    value: undefined
  });
  try {
    installIngestionApiStub({ jobs: [ingestionSummary()], detail: ingestionDetail() });
    window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "删除任务" }));
    const dialog = screen.getByRole("dialog", { name: "确认删除任务" });
    const cancel = within(dialog).getByRole("button", { name: "取消" });
    const confirm = within(dialog).getByRole("button", { name: "确认删除" });
    await waitFor(() => expect(cancel).toHaveFocus());
    fireEvent.keyDown(cancel, { key: "Tab", shiftKey: true });
    expect(confirm).toHaveFocus();
    fireEvent.keyDown(confirm, { key: "Tab" });
    expect(cancel).toHaveFocus();
  } finally {
    if (original) Object.defineProperty(HTMLDialogElement.prototype, "showModal", original);
    else Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
      configurable: true,
      value: undefined
    });
  }
});

test("上传期间禁用文件输入并忽略新的拖放", async () => {
  const user = userEvent.setup();
  const stub = installIngestionApiStub({ jobs: [], deferUpload: true });
  render(<App />);
  await user.click(screen.getByRole("button", { name: "文档入库" }));
  const input = screen.getByLabelText("上传文档或 ZIP");
  const dropZone = input.closest("label");
  if (!dropZone) throw new Error("上传区域不存在");

  await user.upload(input, new File(["one"], "案例一.txt", { type: "text/plain" }));
  expect(input).toBeDisabled();
  expect(dropZone).toHaveAttribute("aria-disabled", "true");
  fireEvent.drop(dropZone, {
    dataTransfer: { files: [new File(["two"], "案例二.txt", { type: "text/plain" })] }
  });
  expect(stub.requests.filter((request) => request.method === "POST")).toHaveLength(1);
  await act(async () => stub.resolveUpload());
});

test("入库 API 测试桩执行完整动作状态矩阵", async () => {
  const details = {
    draft: ingestionDraftPayload({ job_id: "draft" }),
    "failed-retry": ingestionDetail({ job_id: "failed-retry", status: "failed" }),
    "failed-delete": ingestionDetail({ job_id: "failed-delete", status: "failed" }),
    succeeded: ingestionDetail({ job_id: "succeeded" }),
    queued: ingestionDetail({ job_id: "queued", status: "queued" }),
    running: ingestionDetail({ job_id: "running", status: "running" }),
    rolling: ingestionDetail({ job_id: "rolling", status: "rolling_back" })
  };
  installIngestionApiStub({ details });

  await expect(startIngestionJob("draft")).resolves.toMatchObject({ status: "queued" });
  await expect(retryIngestionJob("failed-retry")).resolves.toMatchObject({ status: "queued" });
  await expect(deleteIngestionJob("failed-delete")).resolves.toMatchObject({ status: "deleted" });
  await expect(deleteIngestionJob("succeeded")).resolves.toMatchObject({ status: "deleted" });
  await expect(startIngestionJob("queued")).rejects.toMatchObject({ status: 409 });
  await expect(retryIngestionJob("running")).rejects.toMatchObject({ status: 409 });
  await expect(deleteIngestionJob("running")).rejects.toMatchObject({ status: 409 });
  await expect(deleteIngestionJob("rolling")).rejects.toMatchObject({ status: 409 });
});
