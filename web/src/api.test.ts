import {
  answerQuestion,
  answerQuestionStream,
  createBatchRun,
  deleteBatchRun,
  getBatchRunDetail,
  getBatchRunProgress,
  getStatus,
  listBatchRuns,
  resumeBatchRun,
  retryBatchRow,
  revealSource,
  saveBatchRowBadCase,
  submitBadCase
} from "./api";
import type {
  AnswerStreamEvent,
  BadCaseRequest,
  BatchRowBadCaseRequest,
  CreateBatchRunRequest
} from "./types";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init
  });
}

function sseResponse(events: Array<{ event: string; data: unknown }>): Response {
  const text = events
    .map((event) => `event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`)
    .join("");
  return new Response(text, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" }
  });
}

test("getStatus calls status endpoint", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    expect(input).toBe("/api/status");
    return jsonResponse({
      ok: true,
      data_dir: "data",
      milvus_mode: "lite",
      milvus_target: ".local/milvus/xhbx_rag.db",
      milvus_lite_path: ".local/milvus/xhbx_rag.db",
      milvus_collection: "xhbx_sales_chunks",
      config: { API_KEY: true },
      errors: []
    });
  });

  const status = await getStatus({ fetcher });

  expect(status.ok).toBe(true);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

test("answerQuestion posts typed payload", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(input).toBe("http://127.0.0.1:8000/api/answer");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(
      JSON.stringify({ query: "保单整理有什么作用？", top_n: 20, top_k: 5 })
    );
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    return jsonResponse({
      answer: "保单整理能帮助客户看清保障缺口。",
      citations: [],
      evidence_count: 0
    });
  });

  const result = await answerQuestion(
    { query: "保单整理有什么作用？", top_n: 20, top_k: 5 },
    { baseUrl: "http://127.0.0.1:8000/", fetcher }
  );

  expect(result.answer).toContain("保障缺口");
});

test("answerQuestionStream parses ordered server sent events", async () => {
  const events: AnswerStreamEvent[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(input).toBe("/api/answer/stream");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(
      JSON.stringify({ query: "保单整理有什么作用？", top_n: 20, top_k: 5 })
    );
    return sseResponse([
      {
        event: "step",
        data: {
          type: "step",
          step: "search.query_understood",
          message: "已完成问题理解",
          payload: { rewritten_query: "保单整理客户价值" }
        }
      },
      { event: "answer_delta", data: { type: "answer_delta", text: "保单整理能" } },
      { event: "answer_delta", data: { type: "answer_delta", text: "看清保障缺口。" } },
      {
        event: "final",
        data: {
          type: "final",
          response: {
            answer: "保单整理能看清保障缺口。",
            citations: [],
            evidence_count: 0,
            retrieval_evidences: []
          }
        }
      }
    ]);
  });

  const result = await answerQuestionStream(
    { query: "保单整理有什么作用？", top_n: 20, top_k: 5 },
    {
      onEvent: (event) => events.push(event)
    },
    { fetcher }
  );

  expect(events.map((event) => event.type)).toEqual([
    "step",
    "answer_delta",
    "answer_delta",
    "final"
  ]);
  expect(result.answer).toBe("保单整理能看清保障缺口。");
});

test("answerQuestionStream surfaces streamed errors", async () => {
  const fetcher = vi.fn(async () =>
    sseResponse([
      { event: "error", data: { type: "error", detail: "问答服务暂时不可用" } }
    ])
  );

  await expect(
    answerQuestionStream({ query: "x" }, {}, { fetcher })
  ).rejects.toThrow("问答服务暂时不可用");
});

test("revealSource returns resolved path", async () => {
  const fetcher = vi.fn(async () =>
    jsonResponse({ ok: true, resolved_path: "/tmp/data/a.txt" })
  );

  const result = await revealSource({ source_path: "data/a.txt" }, { fetcher });

  expect(result.resolved_path).toBe("/tmp/data/a.txt");
});

test("submitBadCase posts typed payload", async () => {
  const request: BadCaseRequest = {
    query: "保单整理有什么作用？",
    rewritten_query: "保单整理客户价值",
    answer: "保单整理能帮助客户看清保障缺口。",
    top_n: 20,
    top_k: 5,
    feedback_result: "incomplete",
    problem_tags: ["missing_talk_track"],
    problem_detail: "当前回答偏销售动作。",
    expected_answer: "应该命中客户保障缺口。",
    reference_note: "案例A 第3节",
    evidence_feedback: [
      {
        chunk_id: "case-a-1",
        judgement: "should_use",
        label: "案例A · 需求分析",
        text_preview: "客户需要先看清保障缺口。"
      }
    ],
    issue_types: ["incomplete", "missing_talk_track"],
    expected_knowledge: "应该命中客户保障缺口。",
    expected_source: "案例A 第3节",
    note: "当前回答偏销售动作。",
    citations: [],
    retrieval_evidences: []
  };
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(input).toBe("/api/bad-cases");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify(request));
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    return jsonResponse({ ok: true, bad_case_id: "bad-case-1" });
  });

  const result = await submitBadCase(request, { fetcher });

  expect(result.bad_case_id).toBe("bad-case-1");
});

test("createBatchRun posts typed payload to batch-runs endpoint", async () => {
  const request: CreateBatchRunRequest = {
    title: "qa.csv",
    source_label: "qa.csv",
    source_format: "csv",
    headers: ["问题", "答案"],
    rows: [["保单整理有什么作用？", "原答案"]],
    questions: [
      {
        row_index: 1,
        query: "保单整理有什么作用？",
        input_answer: "原答案",
        top_n: 20,
        top_k: 5
      }
    ]
  };
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(input).toBe("/api/batch-runs");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify(request));
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    return jsonResponse(
      {
        run_id: "run-1",
        title: "qa.csv",
        status: "pending",
        source_label: "qa.csv",
        source_format: "csv",
        question_total: 1,
        question_done: 0,
        question_failed: 0,
        created_at: "2026-07-02T08:00:00+00:00",
        updated_at: "2026-07-02T08:00:00+00:00"
      },
      { status: 201 }
    );
  });

  const summary = await createBatchRun(request, { fetcher });

  expect(summary.run_id).toBe("run-1");
  expect(summary.status).toBe("pending");
});

test("listBatchRuns gets batch run summaries", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(input).toBe("/api/batch-runs");
    expect(init?.method).toBe("GET");
    return jsonResponse({ runs: [] });
  });

  const result = await listBatchRuns({ fetcher });

  expect(result.runs).toEqual([]);
});

test("getBatchRunProgress gets lightweight progress", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(input).toBe("/api/batch-runs/run-1/progress");
    expect(init?.method).toBe("GET");
    return jsonResponse({
      run_id: "run-1",
      status: "running",
      question_total: 2,
      question_done: 1,
      question_failed: 0,
      updated_at: "2026-07-02T08:00:00+00:00",
      questions: [
        { row_index: 1, status: "succeeded", updated_at: "2026-07-02T08:00:00+00:00" },
        { row_index: 2, status: "running", updated_at: "2026-07-02T08:00:00+00:00" }
      ]
    });
  });

  const progress = await getBatchRunProgress("run-1", { fetcher });

  expect(progress.status).toBe("running");
  expect(progress.questions).toHaveLength(2);
});

test("getBatchRunDetail appends include_table only when requested", async () => {
  const urls: string[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    urls.push(String(input));
    return jsonResponse({
      run_id: "run-1",
      title: "qa.csv",
      status: "completed",
      source_label: "qa.csv",
      source_format: "csv",
      question_total: 0,
      question_done: 0,
      question_failed: 0,
      created_at: "2026-07-02T08:00:00+00:00",
      updated_at: "2026-07-02T08:00:00+00:00",
      questions: []
    });
  });

  await getBatchRunDetail("run-1", {}, { fetcher });
  await getBatchRunDetail("run-1", { includeTable: true }, { fetcher });

  expect(urls).toEqual([
    "/api/batch-runs/run-1",
    "/api/batch-runs/run-1?include_table=true"
  ]);
});

test("retryBatchRow posts to the row retry endpoint", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(input).toBe("/api/batch-runs/run-1/rows/3/retry");
    expect(init?.method).toBe("POST");
    return jsonResponse({ ok: true });
  });

  const result = await retryBatchRow("run-1", 3, { fetcher });

  expect(result.ok).toBe(true);
});

test("resumeBatchRun posts to the resume endpoint", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(input).toBe("/api/batch-runs/run-1/resume");
    expect(init?.method).toBe("POST");
    return jsonResponse({ ok: true });
  });

  const result = await resumeBatchRun("run-1", { fetcher });

  expect(result.ok).toBe(true);
});

test("deleteBatchRun issues DELETE and surfaces 409 detail", async () => {
  const okFetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(input).toBe("/api/batch-runs/run-1");
    expect(init?.method).toBe("DELETE");
    return jsonResponse({ ok: true });
  });

  const result = await deleteBatchRun("run-1", { fetcher: okFetcher });
  expect(result.ok).toBe(true);

  const conflictFetcher = vi.fn(async () =>
    jsonResponse({ detail: "批量任务正在执行，无法删除" }, { status: 409 })
  );

  await expect(
    deleteBatchRun("run-1", { fetcher: conflictFetcher })
  ).rejects.toMatchObject({
    status: 409,
    detail: "批量任务正在执行，无法删除"
  });
});

test("saveBatchRowBadCase posts the batch row feedback payload", async () => {
  const request: BatchRowBadCaseRequest = {
    query: "保单整理有什么作用？",
    rewritten_query: "保单整理客户价值",
    answer: "保单整理能帮助客户看清保障缺口。",
    top_n: 20,
    top_k: 5,
    feedback_result: "incomplete",
    problem_tags: ["missing_talk_track"],
    problem_detail: "当前回答偏销售动作。",
    expected_answer: "应该命中客户保障缺口。",
    reference_note: "案例A 第3节",
    evidence_feedback: [],
    issue_types: ["incomplete", "missing_talk_track"],
    expected_knowledge: "应该命中客户保障缺口。",
    expected_source: "案例A 第3节",
    note: "当前回答偏销售动作。",
    citations: [],
    retrieval_evidences: [],
    input_answer: "原答案",
    batch_source_label: "qa.csv"
  };
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(input).toBe("/api/batch-runs/run-1/rows/2/bad-case");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify(request));
    return jsonResponse({ ok: true, bad_case_id: "bad-case-9" });
  });

  const result = await saveBatchRowBadCase("run-1", 2, request, { fetcher });

  expect(result.bad_case_id).toBe("bad-case-9");
});

test("batch api errors expose backend detail", async () => {
  const fetcher = vi.fn(async () =>
    jsonResponse({ detail: "批量会话不存在" }, { status: 404 })
  );

  await expect(
    getBatchRunProgress("missing", { fetcher })
  ).rejects.toMatchObject({
    status: 404,
    detail: "批量会话不存在"
  });
});

test("api errors expose safe detail", async () => {
  const fetcher = vi.fn(async () =>
    jsonResponse({ detail: "问答服务暂时不可用" }, { status: 502 })
  );

  await expect(
    answerQuestion({ query: "x" }, { fetcher })
  ).rejects.toMatchObject({
    status: 502,
    detail: "问答服务暂时不可用"
  });
});
