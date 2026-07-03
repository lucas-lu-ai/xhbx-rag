import { readSheet } from "read-excel-file/universal";
import writeExcelFile from "write-excel-file/node";

import type {
  BatchRunDetail,
  BatchRunProgress,
  BatchRunQuestionDetail,
  BatchRunSummary
} from "./types";

export const statusPayload = {
  ok: true,
  data_dir: "data",
  milvus_mode: "lite",
  milvus_target: ".local/milvus/xhbx_rag.db",
  milvus_lite_path: ".local/milvus/xhbx_rag.db",
  milvus_collection: "xhbx_sales_chunks",
  batch_concurrency: 1,
  config: { API_KEY: true },
  errors: []
};

export const answerPayload = {
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

export function answerPayloadWithCitations(count: number) {
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

export function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init
  });
}

export function sseResponse(
  events: Array<{ event: string; data: unknown }>
): Response {
  return new Response(
    events
      .map(
        (event) => `event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`
      )
      .join(""),
    {
      status: 200,
      headers: { "Content-Type": "text/event-stream" }
    }
  );
}

export function answerStreamResponse() {
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

export type RecordedRequest = {
  url: string;
  method: string;
  body?: unknown;
};

export type FetchHandler = (
  url: string,
  init?: RequestInit
) => Response | Promise<Response> | null | undefined;

// 共享 fetch stub：默认响应 GET /api/batch-runs → {runs: []}，避免存量用例 act 噪音。
export function installFetchStub(handle?: FetchHandler) {
  const requests: RecordedRequest[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    requests.push({
      url,
      method,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined
    });

    const handled = handle?.(url, init);
    if (handled) {
      return handled;
    }

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
    if (url.endsWith("/api/batch-runs") && method === "GET") {
      return jsonResponse({ runs: [] });
    }
    return jsonResponse({ detail: "not found" }, { status: 404 });
  });

  vi.stubGlobal("fetch", fetcher);
  return { fetcher, requests };
}

export function deferredResponse() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

export function installStorageStub() {
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

const cleanupCallbacks: Array<() => void> = [];

export function runRegisteredCleanups() {
  while (cleanupCallbacks.length > 0) {
    cleanupCallbacks.pop()?.();
  }
}

export function installDownloadStub() {
  const textParts: string[] = [];
  const blobs: Blob[] = [];
  const originalCreateElement = document.createElement.bind(document);
  const click = vi.fn();
  const createObjectUrlSpy = vi
    .spyOn(URL, "createObjectURL")
    .mockImplementation((blob) => {
      if (blob instanceof Blob) {
        blobs.push(blob);
        void blob.text().then((text) => textParts.push(text));
      }
      return "blob:batch-download";
    });
  const revokeObjectUrlSpy = vi
    .spyOn(URL, "revokeObjectURL")
    .mockImplementation(() => {});
  const createElementSpy = vi
    .spyOn(document, "createElement")
    .mockImplementation((tagName: string) => {
      const element = originalCreateElement(tagName);
      if (tagName.toLowerCase() === "a") {
        Object.defineProperty(element, "click", { value: click });
      }
      return element;
    });
  let restored = false;
  const restore = () => {
    if (restored) {
      return;
    }
    restored = true;
    createObjectUrlSpy.mockRestore();
    revokeObjectUrlSpy.mockRestore();
    createElementSpy.mockRestore();
  };
  cleanupCallbacks.push(restore);

  return {
    blobs,
    textParts,
    restore
  };
}

export type TestSheetCell = string | number | boolean | Date | null;

export async function makeXlsxFile(
  rows: TestSheetCell[][],
  name = "测试问题.xlsx"
): Promise<File> {
  const buffer = await writeExcelFile(rows).toBuffer();
  return new File([buffer], name, {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
  });
}

export async function readXlsxBlob(blob: Blob): Promise<unknown[][]> {
  return readSheet(blob);
}

export function batchRunSummary(
  overrides: Partial<BatchRunSummary> = {}
): BatchRunSummary {
  return {
    run_id: "run-1",
    title: "批量测试",
    status: "completed",
    source_label: "qa.csv",
    source_format: "csv",
    question_total: 1,
    question_done: 1,
    question_failed: 0,
    created_at: "2026-07-02T08:00:00+00:00",
    updated_at: "2026-07-02T08:05:00+00:00",
    ...overrides
  };
}

export function batchRunQuestionDetail(
  overrides: Partial<BatchRunQuestionDetail> = {}
): BatchRunQuestionDetail {
  return {
    row_index: 1,
    query: "客户说每年不能超过80万怎么办？",
    input_answer: "人工答案",
    top_n: 20,
    top_k: 5,
    status: "succeeded",
    response: { ...answerPayload },
    error: null,
    bad_case: null,
    updated_at: "2026-07-02T08:05:00+00:00",
    ...overrides
  };
}

export function batchRunDetail(
  overrides: Partial<BatchRunDetail> = {}
): BatchRunDetail {
  return {
    ...batchRunSummary(),
    questions: [batchRunQuestionDetail()],
    ...overrides
  };
}

export function batchRunProgressOf(detail: BatchRunDetail): BatchRunProgress {
  return {
    run_id: detail.run_id,
    status: detail.status,
    question_total: detail.question_total,
    question_done: detail.question_done,
    question_failed: detail.question_failed,
    updated_at: detail.updated_at,
    questions: detail.questions.map((question) => ({
      row_index: question.row_index,
      status: question.status,
      updated_at: question.updated_at
    }))
  };
}
