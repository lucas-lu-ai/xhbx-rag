import {
  batchBadCaseSourceLabel,
  batchQuestionStatusLabel,
  batchRunDetailToRunState,
  batchRunProgressSignature,
  batchRunProgressText,
  batchRunStatusLabel,
  buildCreateBatchRunRequest,
  isBatchRunActive,
  latestSessionSelection,
  makeBatchRunTitle,
  mergeSessionEntries
} from "./batchRuns";
import {
  batchRunDetail,
  batchRunQuestionDetail,
  batchRunSummary
} from "./test-utils";
import type { BatchRunState, ChatSession } from "./types";

function makeChatSession(overrides: Partial<ChatSession> = {}): ChatSession {
  return {
    id: "session-1",
    title: "预算异议",
    created_at: "2026-07-01T08:00:00.000Z",
    updated_at: "2026-07-01T08:00:00.000Z",
    turns: [],
    ...overrides
  };
}

function makeRunState(overrides: Partial<BatchRunState> = {}): BatchRunState {
  return {
    source_label: "qa.csv",
    source_format: "csv",
    headers: ["问题", "答案"],
    rows: [["保单整理有什么作用？", "原答案"]],
    running: false,
    questions: [
      {
        id: "row-1",
        row_index: 1,
        query: "保单整理有什么作用？",
        input_answer: "原答案",
        top_n: 20,
        top_k: 5,
        status: "pending",
        process_steps: [],
        streaming_answer: ""
      }
    ],
    ...overrides
  };
}

test("batchRunStatusLabel 返回中文状态标签", () => {
  expect(batchRunStatusLabel("pending")).toBe("排队中");
  expect(batchRunStatusLabel("running")).toBe("运行中");
  expect(batchRunStatusLabel("completed")).toBe("已完成");
  expect(batchRunStatusLabel("interrupted")).toBe("已中断");
});

test("batchQuestionStatusLabel 返回行状态中文标签", () => {
  expect(batchQuestionStatusLabel("pending")).toBe("待运行");
  expect(batchQuestionStatusLabel("running")).toBe("运行中");
  expect(batchQuestionStatusLabel("succeeded")).toBe("已完成");
  expect(batchQuestionStatusLabel("failed")).toBe("失败");
});

test("isBatchRunActive 只把 pending/running 视为非终态", () => {
  expect(isBatchRunActive("pending")).toBe(true);
  expect(isBatchRunActive("running")).toBe(true);
  expect(isBatchRunActive("completed")).toBe(false);
  expect(isBatchRunActive("interrupted")).toBe(false);
});

test("batchRunProgressText 按 done+failed/total 展示进度", () => {
  expect(
    batchRunProgressText({
      question_total: 10,
      question_done: 2,
      question_failed: 1
    })
  ).toBe("3/10");
});

test("makeBatchRunTitle 文件上传取文件名", () => {
  expect(
    makeBatchRunTitle(makeRunState({ source_label: "销售问题.xlsx", source_format: "xlsx" }))
  ).toBe("销售问题.xlsx");
});

test("makeBatchRunTitle 粘贴时取首个问题并截断 32 字", () => {
  expect(
    makeBatchRunTitle(makeRunState({ source_label: "pasted", source_format: "pasted" }))
  ).toBe("保单整理有什么作用？");

  const longQuery = "问".repeat(40);
  const state = makeRunState({
    source_label: "pasted",
    source_format: "pasted",
    questions: [
      {
        id: "row-1",
        row_index: 1,
        query: longQuery,
        input_answer: "",
        top_n: 20,
        top_k: 5,
        status: "pending",
        process_steps: [],
        streaming_answer: ""
      }
    ]
  });
  expect(makeBatchRunTitle(state)).toBe(`${"问".repeat(32)}...`);
});

test("buildCreateBatchRunRequest 组装创建请求", () => {
  const request = buildCreateBatchRunRequest(makeRunState());

  expect(request).toEqual({
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
  });
});

test("batchRunDetailToRunState 映射详情为本地运行状态", () => {
  const badCase = {
    feedback_result: "incomplete",
    batch_source_label: "qa.csv",
    row_index: 1,
    input_answer: "人工答案"
  };
  const detail = batchRunDetail({
    status: "running",
    headers: ["问题", "答案"],
    rows: [["客户说每年不能超过80万怎么办？", ""]],
    questions: [
      batchRunQuestionDetail({ bad_case: badCase }),
      batchRunQuestionDetail({
        row_index: 2,
        query: "失败问题",
        status: "failed",
        response: null,
        error: "问答服务暂时不可用"
      })
    ]
  });

  const state = batchRunDetailToRunState(detail);

  expect(state.source_label).toBe("qa.csv");
  expect(state.source_format).toBe("csv");
  expect(state.headers).toEqual(["问题", "答案"]);
  expect(state.rows).toEqual([["客户说每年不能超过80万怎么办？", ""]]);
  expect(state.running).toBe(true);
  expect(state.questions[0]).toMatchObject({
    id: "row-1",
    row_index: 1,
    status: "succeeded",
    streaming_answer: "先承接预算，再讨论缴费期和保障缺口。",
    bad_case_payload: badCase
  });
  expect(state.questions[0].response?.answer).toBe(
    "先承接预算，再讨论缴费期和保障缺口。"
  );
  expect(state.questions[1]).toMatchObject({
    id: "row-2",
    row_index: 2,
    status: "failed",
    streaming_answer: "",
    error: "问答服务暂时不可用"
  });
  expect(state.questions[1].response).toBeUndefined();
  expect(state.questions[1].bad_case_payload).toBeUndefined();
});

test("batchRunDetailToRunState 缺少表格时回退为空表", () => {
  const state = batchRunDetailToRunState(batchRunDetail());

  expect(state.headers).toEqual([]);
  expect(state.rows).toEqual([]);
});

test("batchBadCaseSourceLabel 粘贴来源归一为 pasted.csv", () => {
  expect(
    batchBadCaseSourceLabel({ source_label: "pasted", source_format: "pasted" })
  ).toBe("pasted.csv");
  expect(
    batchBadCaseSourceLabel({ source_label: "qa.csv", source_format: "csv" })
  ).toBe("qa.csv");
});

test("mergeSessionEntries 按 created_at 降序混排并带稳定 key", () => {
  const chatOld = makeChatSession({
    id: "session-old",
    created_at: "2026-07-01T08:00:00.000Z"
  });
  const chatNew = makeChatSession({
    id: "session-new",
    created_at: "2026-07-02T09:00:00.000Z"
  });
  const run = batchRunSummary({
    run_id: "run-mid",
    created_at: "2026-07-01T12:00:00.000Z"
  });

  const entries = mergeSessionEntries([chatOld, chatNew], [run]);

  expect(entries.map((entry) => entry.key)).toEqual([
    "chat:session-new",
    "batch:run-mid",
    "chat:session-old"
  ]);
});

test("latestSessionSelection 取混排后最新条目", () => {
  const chat = makeChatSession({
    id: "session-1",
    created_at: "2026-07-01T08:00:00.000Z"
  });
  const run = batchRunSummary({
    run_id: "run-1",
    created_at: "2026-07-02T08:00:00.000Z"
  });

  expect(latestSessionSelection(mergeSessionEntries([chat], [run]))).toEqual({
    kind: "batch",
    id: "run-1"
  });
  expect(latestSessionSelection(mergeSessionEntries([chat], []))).toEqual({
    kind: "chat",
    id: "session-1"
  });
  expect(latestSessionSelection([])).toBeNull();
});

test("batchRunProgressSignature 忽略 updated_at 抖动，只反映状态变化", () => {
  const base = {
    status: "running" as const,
    question_done: 1,
    question_failed: 0,
    questions: [
      { row_index: 1, status: "succeeded" as const },
      { row_index: 2, status: "running" as const }
    ]
  };

  expect(batchRunProgressSignature(base)).toBe(
    batchRunProgressSignature({ ...base })
  );
  expect(batchRunProgressSignature(base)).not.toBe(
    batchRunProgressSignature({
      ...base,
      questions: [
        { row_index: 1, status: "succeeded" },
        { row_index: 2, status: "failed" }
      ]
    })
  );
});
