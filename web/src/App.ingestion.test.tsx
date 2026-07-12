import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "./App";
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
