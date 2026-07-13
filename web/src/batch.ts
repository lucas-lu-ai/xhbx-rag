import type {
  BatchBadCaseJsonlRecord,
  BatchQuestion,
  BatchRunState,
  BatchSourceFormat
} from "./types";

type ParseBatchDelimitedInputArgs = {
  text: string;
  sourceLabel: string;
  sourceFormat: BatchSourceFormat;
  topN?: number;
  topK?: number;
};

type ParseBatchTableInputArgs = {
  rows: unknown[][];
  sourceLabel: string;
  sourceFormat: BatchSourceFormat;
  topN?: number;
  topK?: number;
};

type BuildBackfilledDelimitedTextArgs = {
  headers: string[];
  rows: string[][];
  questions: BatchQuestion[];
};

const MAX_BATCH_QUESTIONS = 100;
const DEFAULT_TOP_N = 20;
const DEFAULT_TOP_K = 5;
// 与解析约定保持一致：第一列问题、第二列答案。
export const BATCH_TEMPLATE_TABLE: readonly string[][] = [["问题", "答案"]];
export const BATCH_TEMPLATE_FILE_NAME = "批量问题模板.xlsx";

export function parseBatchDelimitedInput({
  text,
  sourceLabel,
  sourceFormat,
  topN = DEFAULT_TOP_N,
  topK = DEFAULT_TOP_K
}: ParseBatchDelimitedInputArgs): BatchRunState {
  return parseBatchTableInput({
    rows: parseCommaDelimited(text),
    sourceLabel,
    sourceFormat,
    topN,
    topK
  });
}

export function parseBatchTableInput({
  rows: rawRows,
  sourceLabel,
  sourceFormat,
  topN = DEFAULT_TOP_N,
  topK = DEFAULT_TOP_K
}: ParseBatchTableInputArgs): BatchRunState {
  const parsedRows = rawRows.map((row) => row.map(cellToString));
  const headers = parsedRows[0] ?? [];
  if (headers.length < 2) {
    throw new Error("文件必须包含问题和答案两列");
  }

  const rows = parsedRows.slice(1);
  const questions = rows.flatMap((row, index): BatchQuestion[] => {
    const query = (row[0] ?? "").trim();
    if (!query) {
      return [];
    }

    // row_index 是数据行号（不含表头），从 1 开始，与界面“第 N 行”展示一致。
    const rowIndex = index + 1;
    return [
      {
        id: `row-${rowIndex}`,
        row_index: rowIndex,
        query,
        input_answer: row[1] ?? "",
        top_n: topN,
        top_k: topK,
        status: "pending",
        process_steps: [],
        streaming_answer: ""
      }
    ];
  });

  if (questions.length === 0) {
    throw new Error("没有解析到可执行的问题");
  }
  if (questions.length > MAX_BATCH_QUESTIONS) {
    throw new Error("单批最多支持 100 个问题，请拆分后再运行");
  }

  return {
    source_label: sourceLabel,
    source_format: sourceFormat,
    headers,
    rows,
    questions,
    running: false
  };
}

export function buildBackfilledDelimitedText({
  headers,
  rows,
  questions
}: BuildBackfilledDelimitedTextArgs): string {
  return serializeCommaDelimited(buildBackfilledTable({ headers, rows, questions }));
}

export function buildBackfilledTable({
  headers,
  rows,
  questions
}: BuildBackfilledDelimitedTextArgs): string[][] {
  const succeededAnswers = new Map<number, string>();
  for (const question of questions) {
    if (question.status === "succeeded" && question.response) {
      succeededAnswers.set(question.row_index, question.response.answer);
    }
  }

  const backfilledRows = rows.map((row, index) => {
    const rowIndex = index + 1;
    const answer = succeededAnswers.get(rowIndex);
    if (answer === undefined) {
      return row;
    }

    const nextRow = [...row];
    nextRow[1] = answer;
    return nextRow;
  });

  return [headers, ...backfilledRows];
}

export function buildBadCaseJsonl(records: BatchBadCaseJsonlRecord[]): string {
  const badCaseRecords = records.filter((record) => record.feedback_result !== "usable");
  if (badCaseRecords.length === 0) {
    return "";
  }
  return `${badCaseRecords.map((record) => JSON.stringify(record)).join("\n")}\n`;
}

export function backfilledDownloadName(sourceLabel: string): string {
  const fileName = sourceLabel.trim().split(/[\\/]/).pop() ?? "";
  if (!fileName || fileName === "pasted") {
    return "batch-backfilled.csv";
  }

  const extensionIndex = fileName.lastIndexOf(".");
  if (extensionIndex <= 0 || extensionIndex === fileName.length - 1) {
    return "batch-backfilled.csv";
  }

  const baseName = fileName.slice(0, extensionIndex);
  const extension = fileName.slice(extensionIndex);
  return `${baseName}-backfilled${extension}`;
}

export function badCaseJsonlDownloadName(sourceLabel: string): string {
  return `${downloadBaseName(sourceLabel)}-bad-cases.jsonl`;
}

function parseCommaDelimited(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];

    if (inQuotes) {
      if (char === "\"") {
        const nextChar = text[index + 1];
        if (nextChar === "\"") {
          field += "\"";
          index += 1;
        } else {
          inQuotes = false;
        }
      } else {
        field += char;
      }
      continue;
    }

    if (char === "\"") {
      if (field.length === 0) {
        inQuotes = true;
      } else {
        field += char;
      }
      continue;
    }
    if (char === ",") {
      row.push(field);
      field = "";
      continue;
    }
    if (char === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
      continue;
    }
    if (char === "\r") {
      if (text[index + 1] === "\n") {
        index += 1;
      }
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
      continue;
    }

    field += char;
  }

  if (inQuotes) {
    throw new Error("CSV 引号未闭合");
  }

  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }

  return rows;
}

function serializeCommaDelimited(rows: string[][]): string {
  return rows.map((row) => row.map(serializeCell).join(",")).join("\n");
}

function serializeCell(value: string): string {
  if (!/[",\r\n]/.test(value)) {
    return value;
  }
  return `"${value.replaceAll("\"", "\"\"")}"`;
}

function cellToString(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  if (value instanceof Date) {
    return value.toISOString();
  }
  return String(value);
}

function downloadBaseName(sourceLabel: string): string {
  const fileName = sourceLabel.trim().split(/[\\/]/).pop() ?? "";
  const withoutExtension = fileName.replace(/\.[^.]*$/, "");
  return withoutExtension || "batch";
}
