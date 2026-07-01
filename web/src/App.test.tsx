import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "./App";

const statusPayload = {
  ok: true,
  data_dir: "data",
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
  rewritten_query: "客户预算上限80万时如何回应"
};

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init
  });
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
    if (url.endsWith("/api/answer")) {
      return jsonResponse(answerPayload);
    }
    if (url.endsWith("/api/source/reveal")) {
      return jsonResponse({ ok: true, resolved_path: "/tmp/data/a.txt" });
    }
    return jsonResponse({ detail: "not found" }, { status: 404 });
  });

  vi.stubGlobal("fetch", fetcher);
  return { fetcher, requests };
}

afterEach(() => {
  vi.unstubAllGlobals();
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
  expect(requests).toContainEqual({
    url: "/api/answer",
    body: { query: "客户说每年不能超过80万怎么办？", top_n: 20, top_k: 5 }
  });
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
