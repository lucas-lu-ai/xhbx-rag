import { isObject } from "./format";
import type {
  BatchBadCaseJsonlRecord,
  BatchQuestionStatus,
  BatchRunDetail,
  BatchRunQuestionDetail,
  BatchRunState,
  BatchRunStatus,
  BatchRunSummary,
  ChatSession,
  ChatTurn,
  CreateBatchRunRequest,
  SessionSelection
} from "./types";

const DEFAULT_BATCH_RUN_TITLE = "批量执行";
const MAX_BATCH_RUN_TITLE_LENGTH = 32;

export const BATCH_RUN_STATUS_LABELS: Record<BatchRunStatus, string> = {
  pending: "排队中",
  running: "运行中",
  completed: "已完成",
  interrupted: "已中断"
};

const BATCH_QUESTION_STATUS_LABELS: Record<BatchQuestionStatus, string> = {
  pending: "待运行",
  running: "运行中",
  succeeded: "已完成",
  failed: "失败"
};

export function batchRunStatusLabel(status: BatchRunStatus): string {
  return BATCH_RUN_STATUS_LABELS[status] ?? status;
}

export function batchQuestionStatusLabel(status: BatchQuestionStatus): string {
  return BATCH_QUESTION_STATUS_LABELS[status] ?? status;
}

export function isBatchRunActive(status: BatchRunStatus): boolean {
  return status === "pending" || status === "running";
}

export function batchRunProgressText(
  run: Pick<BatchRunSummary, "question_total" | "question_done" | "question_failed">
): string {
  return `${run.question_done + run.question_failed}/${run.question_total}`;
}

export function makeBatchRunTitle(
  state: Pick<BatchRunState, "source_label" | "source_format" | "questions">
): string {
  if (state.source_format !== "pasted") {
    return state.source_label;
  }
  const firstQuery = state.questions[0]?.query.replace(/\s+/g, " ").trim() ?? "";
  if (!firstQuery) {
    return DEFAULT_BATCH_RUN_TITLE;
  }
  if (firstQuery.length <= MAX_BATCH_RUN_TITLE_LENGTH) {
    return firstQuery;
  }
  return `${firstQuery.slice(0, MAX_BATCH_RUN_TITLE_LENGTH)}...`;
}

export function buildCreateBatchRunRequest(
  state: BatchRunState
): CreateBatchRunRequest {
  return {
    title: makeBatchRunTitle(state),
    source_label: state.source_label,
    source_format: state.source_format,
    headers: state.headers,
    rows: state.rows,
    questions: state.questions.map((question) => ({
      row_index: question.row_index,
      query: question.query,
      input_answer: question.input_answer,
      top_n: question.top_n,
      top_k: question.top_k
    }))
  };
}

// 把后端详情映射成本地 BatchRunState，便于无损复用 batch.ts 的导出纯函数。
export function batchRunDetailToRunState(detail: BatchRunDetail): BatchRunState {
  return {
    source_label: detail.source_label,
    source_format: detail.source_format,
    headers: detail.headers ?? [],
    rows: detail.rows ?? [],
    running: isBatchRunActive(detail.status),
    questions: detail.questions.map((question) => ({
      id: `row-${question.row_index}`,
      row_index: question.row_index,
      query: question.query,
      input_answer: question.input_answer,
      top_n: question.top_n,
      top_k: question.top_k,
      status: question.status,
      process_steps: [],
      streaming_answer: question.response?.answer ?? "",
      response: question.response ?? undefined,
      error: question.error ?? undefined,
      bad_case_payload: toBadCaseJsonlRecord(question.bad_case)
    }))
  };
}

function toBadCaseJsonlRecord(
  value: Record<string, unknown> | null
): BatchBadCaseJsonlRecord | undefined {
  if (!isObject(value)) {
    return undefined;
  }
  return value as unknown as BatchBadCaseJsonlRecord;
}

export function batchBadCaseSourceLabel(
  run: Pick<BatchRunSummary, "source_label" | "source_format">
): string {
  return run.source_format === "pasted" && run.source_label === "pasted"
    ? "pasted.csv"
    : run.source_label;
}

export function batchQuestionDetailToChatTurn(
  question: BatchRunQuestionDetail
): ChatTurn {
  return {
    id: `row-${question.row_index}`,
    query: question.query,
    top_n: question.top_n,
    top_k: question.top_k,
    process_steps: [],
    streaming_answer: question.response?.answer ?? "",
    response: question.response ?? undefined,
    error: question.error ?? undefined,
    is_streaming: question.status === "running"
  };
}

export type SessionEntry =
  | { kind: "chat"; key: string; created_at: string; session: ChatSession }
  | { kind: "batch"; key: string; created_at: string; run: BatchRunSummary };

export function mergeSessionEntries(
  sessions: ChatSession[],
  runs: BatchRunSummary[]
): SessionEntry[] {
  const chatEntries = sessions.map((session): SessionEntry => ({
    kind: "chat",
    key: `chat:${session.id}`,
    created_at: session.created_at,
    session
  }));
  const batchEntries = runs.map((run): SessionEntry => ({
    kind: "batch",
    key: `batch:${run.run_id}`,
    created_at: run.created_at,
    run
  }));
  return [...chatEntries, ...batchEntries].sort(
    (left, right) =>
      new Date(right.created_at).getTime() - new Date(left.created_at).getTime()
  );
}

export function latestSessionSelection(
  entries: SessionEntry[]
): SessionSelection | null {
  const first = entries[0];
  if (!first) {
    return null;
  }
  return first.kind === "chat"
    ? { kind: "chat", id: first.session.id }
    : { kind: "batch", id: first.run.run_id };
}

// 进度签名：轮询时只有签名变化才重新拉详情，忽略 updated_at 抖动。
export function batchRunProgressSignature(progress: {
  status: BatchRunStatus;
  question_done: number;
  question_failed: number;
  questions: ReadonlyArray<{ row_index: number; status: BatchQuestionStatus }>;
}): string {
  const rows = progress.questions
    .map((question) => `${question.row_index}:${question.status}`)
    .join(",");
  return `${progress.status}|${progress.question_done}|${progress.question_failed}|${rows}`;
}
