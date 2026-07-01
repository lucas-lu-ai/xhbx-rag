import {
  backfilledDownloadName,
  badCaseJsonlDownloadName,
  buildBackfilledDelimitedText,
  buildBadCaseJsonl,
  parseBatchDelimitedInput
} from "./batch";
import type { BatchBadCaseJsonlRecord, BatchQuestion } from "./types";

type QuestionOverrides = Omit<
  Partial<BatchQuestion>,
  "row_index" | "query" | "input_answer"
> &
  Pick<BatchQuestion, "row_index" | "query" | "input_answer">;

function makeQuestion({
  row_index,
  query,
  input_answer,
  ...overrides
}: QuestionOverrides): BatchQuestion {
  return {
    id: `row-${row_index}`,
    row_index,
    query,
    input_answer,
    top_n: 20,
    top_k: 5,
    status: "pending",
    process_steps: [],
    streaming_answer: "",
    ...overrides
  };
}

function makeBadCaseRecord(overrides: Partial<BatchBadCaseJsonlRecord> = {}): BatchBadCaseJsonlRecord {
  return {
    query: "保单整理有什么作用？",
    rewritten_query: "保单整理客户价值",
    answer: "保单整理能帮助客户看清保障缺口。",
    top_n: 20,
    top_k: 5,
    feedback_result: "incomplete",
    problem_tags: ["missing_talk_track"],
    problem_detail: "当前回答缺少客户视角。",
    expected_answer: "应说明客户能看清保障缺口。",
    reference_note: "案例A 第3节",
    evidence_feedback: [],
    issue_types: ["incomplete", "missing_talk_track"],
    expected_knowledge: "客户保障缺口",
    expected_source: "案例A 第3节",
    note: "批量反馈",
    citations: [],
    retrieval_evidences: [],
    batch_source_label: "qa.csv",
    row_index: 2,
    input_answer: "原答案",
    ...overrides
  };
}

test("parseBatchDelimitedInput 解析带表头的 txt 逗号分隔问题和原始答案", () => {
  const state = parseBatchDelimitedInput({
    text: "问题,答案\n保单整理有什么作用？,原答案\n复购怎么沟通？,旧答案",
    sourceLabel: "sales.txt",
    sourceFormat: "txt"
  });

  expect(state.source_label).toBe("sales.txt");
  expect(state.source_format).toBe("txt");
  expect(state.headers).toEqual(["问题", "答案"]);
  expect(state.rows).toEqual([
    ["保单整理有什么作用？", "原答案"],
    ["复购怎么沟通？", "旧答案"]
  ]);
  expect(state.questions).toEqual([
    makeQuestion({ row_index: 2, query: "保单整理有什么作用？", input_answer: "原答案" }),
    makeQuestion({ row_index: 3, query: "复购怎么沟通？", input_answer: "旧答案" })
  ]);
  expect(state.running).toBe(false);
  expect(state.active_question_id).toBeUndefined();
});

test("parseBatchDelimitedInput 处理双引号字段、逗号、字段内换行和转义双引号", () => {
  const state = parseBatchDelimitedInput({
    text:
      '问题,答案,标签\n' +
      '"客户问：""保单整理""，怎么答？","先确认需求，再说明价值","话术,高频"\n' +
      '"多行问题\n第二行","多行答案\n第二行",备注',
    sourceLabel: "quoted.csv",
    sourceFormat: "csv"
  });

  expect(state.headers).toEqual(["问题", "答案", "标签"]);
  expect(state.rows).toEqual([
    ["客户问：\"保单整理\"，怎么答？", "先确认需求，再说明价值", "话术,高频"],
    ["多行问题\n第二行", "多行答案\n第二行", "备注"]
  ]);
  expect(state.questions.map((question) => question.row_index)).toEqual([2, 3]);
  expect(state.questions[0].query).toBe("客户问：\"保单整理\"，怎么答？");
  expect(state.questions[1].input_answer).toBe("多行答案\n第二行");
});

test("parseBatchDelimitedInput 保留未包裹字段中的普通双引号", () => {
  const state = parseBatchDelimitedInput({
    text: '问题,答案\n客户说"太贵了"怎么办？,原答案',
    sourceLabel: "plain-quote.csv",
    sourceFormat: "csv"
  });

  expect(state.rows).toEqual([['客户说"太贵了"怎么办？', "原答案"]]);
  expect(state.questions[0].query).toBe('客户说"太贵了"怎么办？');
});

test("parseBatchDelimitedInput 未闭合引号时报错", () => {
  expect(() =>
    parseBatchDelimitedInput({
      text: '问题,答案\n"未闭合问题,原答案\n下个问题,答案',
      sourceLabel: "unclosed.csv",
      sourceFormat: "csv"
    })
  ).toThrow("CSV 引号未闭合");
});

test("parseBatchDelimitedInput 少于两列时报错", () => {
  expect(() =>
    parseBatchDelimitedInput({
      text: "问题\n保单整理有什么作用？",
      sourceLabel: "bad.csv",
      sourceFormat: "csv"
    })
  ).toThrow("文件必须包含问题和答案两列");
});

test("parseBatchDelimitedInput 无可执行问题时报错", () => {
  expect(() =>
    parseBatchDelimitedInput({
      text: "问题,答案\n,原答案\n   ,旧答案",
      sourceLabel: "empty.csv",
      sourceFormat: "csv"
    })
  ).toThrow("没有解析到可执行的问题");
});

test("parseBatchDelimitedInput 超过 100 条问题时报错", () => {
  const rows = Array.from({ length: 101 }, (_, index) => `问题${index + 1},答案${index + 1}`);

  expect(() =>
    parseBatchDelimitedInput({
      text: ["问题,答案", ...rows].join("\n"),
      sourceLabel: "too-many.csv",
      sourceFormat: "csv"
    })
  ).toThrow("单批最多支持 100 个问题，请拆分后再运行");
});

test("buildBackfilledDelimitedText 保留表头、行顺序和额外列并只回填成功行", () => {
  const output = buildBackfilledDelimitedText({
    headers: ["问题", "答案", "标签"],
    rows: [
      ["保单整理有什么作用？", "原答案", "基础"],
      ["客户问逗号,怎么办？", "旧答案", "话术,高频"],
      ["引用怎么说？", "保留\"原答案\"", "备注"]
    ],
    questions: [
      makeQuestion({
        row_index: 2,
        query: "保单整理有什么作用？",
        input_answer: "原答案",
        status: "succeeded",
        response: {
          answer: "模型答案",
          citations: [],
          evidence_count: 0
        }
      }),
      makeQuestion({
        row_index: 3,
        query: "客户问逗号,怎么办？",
        input_answer: "旧答案",
        status: "failed",
        error: "服务异常"
      }),
      makeQuestion({
        row_index: 4,
        query: "引用怎么说？",
        input_answer: "保留\"原答案\"",
        status: "succeeded",
        response: {
          answer: "带逗号, 和 \"引用\"\n第二行",
          citations: [],
          evidence_count: 0
        }
      })
    ]
  });

  expect(output).toBe(
    [
      "问题,答案,标签",
      "保单整理有什么作用？,模型答案,基础",
      "\"客户问逗号,怎么办？\",旧答案,\"话术,高频\"",
      "引用怎么说？,\"带逗号, 和 \"\"引用\"\"\n第二行\",备注"
    ].join("\n")
  );
});

test("buildBadCaseJsonl 只序列化非 usable bad case records 且末尾带换行", () => {
  const records = [
    makeBadCaseRecord({ row_index: 2, input_answer: "原答案" }),
    makeBadCaseRecord({
      row_index: 3,
      input_answer: "可用原答案",
      feedback_result: "usable",
      problem_tags: [],
      issue_types: ["usable"]
    }),
    makeBadCaseRecord({
      row_index: 5,
      query: "第二个问题",
      input_answer: "第二个原答案",
      batch_source_label: "pasted"
    })
  ];

  const output = buildBadCaseJsonl(records);
  const expectedRecords = records.filter((record) => record.feedback_result !== "usable");

  expect(output).toBe(`${expectedRecords.map((record) => JSON.stringify(record)).join("\n")}\n`);
});

test("buildBadCaseJsonl 过滤后没有记录时返回空字符串", () => {
  const output = buildBadCaseJsonl([
    makeBadCaseRecord({
      feedback_result: "usable",
      problem_tags: [],
      issue_types: ["usable"]
    })
  ]);

  expect(output).toBe("");
});

test("download name helpers 基于源文件名和扩展生成导出文件名", () => {
  expect(backfilledDownloadName("sales.qa.csv")).toBe("sales.qa-backfilled.csv");
  expect(backfilledDownloadName("sales.txt")).toBe("sales-backfilled.txt");
  expect(backfilledDownloadName("sales.csv")).toBe("sales-backfilled.csv");
  expect(backfilledDownloadName("pasted")).toBe("batch-backfilled.csv");
  expect(badCaseJsonlDownloadName("sales.qa.csv")).toBe("sales.qa-bad-cases.jsonl");
  expect(backfilledDownloadName("")).toBe("batch-backfilled.csv");
  expect(badCaseJsonlDownloadName("")).toBe("batch-bad-cases.jsonl");
});
