import { readSheet } from "read-excel-file/universal";
import writeExcelFile from "write-excel-file/node";

import type {
  BatchRunDetail,
  BatchRunProgress,
  BatchRunQuestionDetail,
  BatchRunSummary,
  IngestionJobDetail,
  IngestionJobProgress,
  IngestionJobSummary
} from "./types";

export const statusPayload = {
  ok: true,
  data_dir: "data",
  milvus_mode: "lite",
  milvus_target: ".local/milvus/xhbx_rag.db",
  milvus_lite_path: ".local/milvus/xhbx_rag.db",
  milvus_collection: "xhbx_sales_chunks",
  milvus_course_collection: "xhbx_course_chunks",
  milvus_collections: ["xhbx_sales_chunks", "xhbx_course_chunks"],
  batch_concurrency: 1,
  web_retrieval_top_n: 20,
  web_retrieval_top_k: 5,
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
      can_reveal: true,
      selected: true,
      evidence_index: 1
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

// 构造多证据回答：citedIndexes 指定答案引用了哪些证据（1-based）。
export function answerPayloadWithEvidences(
  count: number,
  citedIndexes: number[] = [1]
) {
  return {
    ...answerPayload,
    citations: citedIndexes.map((evidenceIndex) => ({
      filename: `第${evidenceIndex}节.track-0.txt`,
      source_type: "txt",
      source_path: `data/案例A/第${evidenceIndex}节.track-0.txt`,
      display_location: `L${evidenceIndex}`,
      display_excerpt: `第${evidenceIndex}条引用原文`,
      locator_confidence: "validated_span",
      can_reveal: true,
      selected: true,
      evidence_index: evidenceIndex
    })),
    evidence_count: count,
    retrieval_evidences: Array.from({ length: count }, (_, index) => ({
      chunk_id: `case-a-${index + 1}`,
      chunk_type: "objection_handling",
      text: `证据${index + 1}正文内容。`,
      rerank_score: 0.9 - index * 0.1,
      metadata: { case_name: "案例A", stage: `阶段${index + 1}` },
      citations: [
        {
          filename: `第${index + 1}节.track-0.txt`,
          source_type: "txt",
          source_path: `data/案例A/第${index + 1}节.track-0.txt`,
          display_location: `L${index + 1}`,
          display_excerpt: `第${index + 1}条引用原文`,
          can_reveal: true
        }
      ]
    }))
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

export function ingestionSummary(
  overrides: Partial<IngestionJobSummary> = {}
): IngestionJobSummary {
  return {
    job_id: "job-1",
    source_name: "优秀案例.zip",
    source_kind: "zip",
    target: "case",
    status: "succeeded",
    current_stage: "completed",
    attempt_count: 1,
    item_total: 3,
    item_done: 3,
    document_total: 6,
    chunk_total: 24,
    ignored_total: 1,
    warning_count: 0,
    error_code: null,
    error_detail: null,
    created_at: "2026-07-10T08:00:00+00:00",
    updated_at: "2026-07-10T08:05:00+00:00",
    started_at: "2026-07-10T08:01:00+00:00",
    finished_at: "2026-07-10T08:05:00+00:00",
    ...overrides
  };
}

export function ingestionDetail(
  overrides: Partial<IngestionJobDetail> = {}
): IngestionJobDetail {
  return {
    ...ingestionSummary(),
    ignored_entries: ["__MACOSX/._说明.txt"],
    items: [
      {
        item_index: 1,
        unit_key: "王女士年金险案例",
        display_name: "王女士年金险案例",
        relative_paths: ["王女士年金险案例/沟通记录.txt"],
        document_count: 2,
        status: "succeeded",
        current_stage: "completed",
        chunk_count: 8,
        warning_count: 0,
        error_detail: null,
        updated_at: "2026-07-10T08:05:00+00:00"
      },
      {
        item_index: 2,
        unit_key: "李先生养老规划",
        display_name: "李先生养老规划",
        relative_paths: ["李先生养老规划/需求说明.pdf"],
        document_count: 2,
        status: "succeeded",
        current_stage: "completed",
        chunk_count: 9,
        warning_count: 0,
        error_detail: null,
        updated_at: "2026-07-10T08:05:00+00:00"
      },
      {
        item_index: 3,
        unit_key: "赵先生保障方案",
        display_name: "赵先生保障方案",
        relative_paths: ["赵先生保障方案/方案.docx"],
        document_count: 2,
        status: "succeeded",
        current_stage: "completed",
        chunk_count: 7,
        warning_count: 0,
        error_detail: null,
        updated_at: "2026-07-10T08:05:00+00:00"
      }
    ],
    attempt: {
      attempt_no: 1,
      status: "succeeded",
      current_stage: "completed",
      commit_state: "committed",
      error_code: null,
      error_detail: null,
      started_at: "2026-07-10T08:01:00+00:00",
      finished_at: "2026-07-10T08:05:00+00:00"
    },
    events: [
      {
        attempt_no: 1,
        sequence: 1,
        event_type: "attempt_started",
        message: "任务开始处理",
        payload: { internal_path: "/private/secret", token: "do-not-render" },
        created_at: "2026-07-10T08:01:00+00:00"
      }
    ],
    ...overrides
  };
}

export function ingestionDraftPayload(
  overrides: Partial<IngestionJobDetail> = {}
): IngestionJobDetail {
  const detail = ingestionDetail({
    status: "draft",
    current_stage: "uploaded",
    attempt_count: 0,
    item_done: 0,
    chunk_total: 0,
    started_at: null,
    finished_at: null,
    attempt: null,
    events: [],
    items: ingestionDetail().items.map((item) => ({
      ...item,
      status: "pending",
      current_stage: "uploaded",
      chunk_count: 0
    }))
  });
  return { ...detail, ...overrides };
}

type IngestionApiStubOptions = {
  draft?: IngestionJobDetail;
  jobs?: IngestionJobSummary[];
  detail?: IngestionJobDetail;
  details?: Record<string, IngestionJobDetail>;
  progress?: IngestionJobProgress | Record<string, IngestionJobProgress>;
  listError?: string;
  deferUpload?: boolean;
  responses?: {
    list?: StubResponse[];
    detail?: Record<string, StubResponse[]>;
    progress?: Record<string, StubResponse[]>;
    start?: Record<string, StubResponse[]>;
    retry?: Record<string, StubResponse[]>;
    delete?: Record<string, StubResponse[]>;
  };
};

type StubResponse = Response | Promise<Response>;

export function installIngestionApiStub(
  options: IngestionApiStubOptions = {}
) {
  const details = new Map<string, IngestionJobDetail>(
    Object.entries(options.details ?? (options.detail ? { [options.detail.job_id]: options.detail } : {}))
  );
  if (options.draft) details.set(options.draft.job_id, options.draft);
  let jobs = options.jobs ?? (options.detail ? [summaryOf(options.detail)] : []);
  let completeUpload: (() => void) | undefined;

  const installed = installFetchStub((url, init) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/ingestion-jobs") && method === "GET") {
      const queued = shiftResponse(options.responses?.list);
      if (queued) return queued;
      if (options.listError) {
        return jsonResponse({ detail: options.listError }, { status: 503 });
      }
      return jsonResponse({ jobs });
    }
    const match = /\/api\/ingestion-jobs\/([^/?]+)(?:\/(progress|start|retry))?$/.exec(url);
    if (!match) return null;
    const jobId = decodeURIComponent(match[1]);
    const operation = match[2];
    const currentDetail = details.get(jobId);

    if (operation === "progress" && method === "GET") {
      const queued = shiftResponse(options.responses?.progress?.[jobId]);
      if (queued) return queued;
      if (!currentDetail) return jsonResponse({ detail: "任务不存在" }, { status: 404 });
      const configured = isProgressMap(options.progress) ? options.progress[jobId] : options.progress;
      return jsonResponse(configured ?? progressOf(currentDetail));
    }
    if (operation === "start" && method === "POST") {
      const queued = shiftResponse(options.responses?.start?.[jobId]);
      if (queued) return queued;
      if (!currentDetail) return jsonResponse({ detail: "任务不存在" }, { status: 404 });
      if (currentDetail.status !== "draft") {
        return jsonResponse({ detail: "仅待确认任务可以开始" }, { status: 409 });
      }
      const next = { ...currentDetail, status: "queued" as const };
      details.set(jobId, next);
      jobs = upsertIngestionSummary(jobs, summaryOf(next));
      return jsonResponse({ ok: true, job_id: jobId, status: "queued" });
    }
    if (operation === "retry" && method === "POST") {
      const queued = shiftResponse(options.responses?.retry?.[jobId]);
      if (queued) return queued;
      if (!currentDetail) return jsonResponse({ detail: "任务不存在" }, { status: 404 });
      if (currentDetail.status !== "failed") {
        return jsonResponse({ detail: "仅失败任务可以重试" }, { status: 409 });
      }
      const next = {
        ...currentDetail,
        status: "queued" as const,
        attempt_count: currentDetail.attempt_count + 1,
        error_code: null,
        error_detail: null
      };
      details.set(jobId, next);
      jobs = upsertIngestionSummary(jobs, summaryOf(next));
      return jsonResponse({
        ok: true,
        job_id: jobId,
        status: "queued",
        attempt_no: next.attempt_count
      });
    }
    if (!operation && method === "DELETE") {
      const queued = shiftResponse(options.responses?.delete?.[jobId]);
      if (queued) return queued;
      if (!currentDetail) return jsonResponse({ detail: "任务不存在" }, { status: 404 });
      if (!["draft", "succeeded", "failed", "deleting"].includes(currentDetail.status)) {
        return jsonResponse({ detail: "当前任务状态不能删除" }, { status: 409 });
      }
      details.delete(jobId);
      jobs = jobs.filter((job) => job.job_id !== jobId);
      return jsonResponse({ ok: true, job_id: jobId, status: "deleted" });
    }
    if (!operation && method === "GET") {
      const queued = shiftResponse(options.responses?.detail?.[jobId]);
      if (queued) return queued;
      return currentDetail
        ? jsonResponse(currentDetail)
        : jsonResponse({ detail: "任务不存在" }, { status: 404 });
    }
    return null;
  });

  class StubXMLHttpRequest extends EventTarget {
    readonly upload = new EventTarget();
    status = 0;
    responseText = "";
    timeout = 0;
    private aborted = false;
    private method = "GET";
    private url = "";

    open(method: string, url: string) {
      this.method = method;
      this.url = url;
    }

    send(body?: Document | XMLHttpRequestBodyInit | null) {
      installed.requests.push({ url: this.url, method: this.method, body });
      const complete = () => {
        if (this.aborted) {
          return;
        }
        this.upload.dispatchEvent(
          new ProgressEvent("progress", {
            lengthComputable: true,
            loaded: 1,
            total: 1
          })
        );
        const uploadedDetail = options.draft ?? ingestionDraftPayload();
        details.set(uploadedDetail.job_id, uploadedDetail);
        jobs = upsertIngestionSummary(jobs, summaryOf(uploadedDetail));
        this.status = 201;
        this.responseText = JSON.stringify(uploadedDetail);
        this.dispatchEvent(new Event("load"));
      };
      if (options.deferUpload) completeUpload = complete;
      else queueMicrotask(complete);
    }

    abort() {
      if (this.aborted) {
        return;
      }
      this.aborted = true;
      this.dispatchEvent(new Event("abort"));
    }
  }

  vi.stubGlobal("XMLHttpRequest", StubXMLHttpRequest);
  return {
    ...installed,
    get jobs() {
      return jobs;
    },
    setJobs(nextJobs: IngestionJobSummary[]) {
      jobs = nextJobs;
    },
    setDetail(nextDetail: IngestionJobDetail) {
      details.set(nextDetail.job_id, nextDetail);
      jobs = upsertIngestionSummary(jobs, summaryOf(nextDetail));
    },
    removeDetail(jobId: string) {
      details.delete(jobId);
      jobs = jobs.filter((job) => job.job_id !== jobId);
    },
    resolveUpload() {
      const complete = completeUpload;
      completeUpload = undefined;
      complete?.();
    }
  };
}

function shiftResponse(queue: StubResponse[] | undefined): StubResponse | undefined {
  return queue?.shift();
}

function isProgressMap(
  value: IngestionApiStubOptions["progress"]
): value is Record<string, IngestionJobProgress> {
  return value !== undefined && !("job_id" in value);
}

function summaryOf(detail: IngestionJobDetail): IngestionJobSummary {
  const {
    ignored_entries: _ignoredEntries,
    items: _items,
    attempt: _attempt,
    events: _events,
    ...summary
  } = detail;
  return summary;
}

function progressOf(detail: IngestionJobDetail): IngestionJobProgress {
  return {
    job_id: detail.job_id,
    status: detail.status,
    current_stage: detail.current_stage,
    attempt_no: detail.attempt?.attempt_no ?? null,
    item_total: detail.item_total,
    item_done: detail.item_done,
    document_total: detail.document_total,
    chunk_total: detail.chunk_total,
    warning_count: detail.warning_count,
    active_item_index:
      detail.items.find((item) => item.status === "running")?.item_index ?? null,
    message: null,
    updated_at: detail.updated_at
  };
}

function upsertIngestionSummary(
  jobs: IngestionJobSummary[],
  summary: IngestionJobSummary
): IngestionJobSummary[] {
  return [summary, ...jobs.filter((job) => job.job_id !== summary.job_id)];
}
