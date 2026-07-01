# Web Batch QA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Web batch question runner that reads two-column comma-separated `.txt/.csv` input, serially runs existing RAG streaming answers, backfills the answer column, and exports bad case JSONL for non-usable feedback.

**Architecture:** Keep the backend API unchanged. Add pure frontend batch helpers for CSV parsing, CSV serialization, answer-column backfill, and bad case JSONL export; then wire React state into the existing `answerQuestionStream`, `CitationList`, `ProcessTimeline`, `EvidenceList`, and `BadCasePanel` components.

**Tech Stack:** React 19, TypeScript, Vite, Vitest, Testing Library, existing FastAPI `/api/answer/stream` and `/api/bad-cases`.

---

## File Structure

- Create `web/src/batch.ts`: pure helpers for parsing comma-separated input, serializing CSV, building backfilled output text, building bad case JSONL, and download filenames.
- Create `web/src/batch.test.ts`: unit tests for parser and export helpers.
- Modify `web/src/types.ts`: add `BatchQuestionStatus`, `BatchQuestion`, `BatchRunState`, `BatchBadCaseJsonlRecord`, and `BatchSourceFormat`.
- Modify `web/src/App.tsx`: add single/batch mode switch, file upload and pasted table parsing, serial execution, per-row retry, output downloads, and batch bad case JSONL capture.
- Modify `web/src/App.test.tsx`: add UI tests for parsing, serial execution, failure isolation, answer backfill download, and bad case JSONL export.
- Modify `web/src/styles.css`: add styles for mode switch, batch input, batch result rows, status chips, and compact batch actions.

## Shared Decisions

- Treat both `.txt` and `.csv` as CSV-formatted text.
- Require a header row and at least two columns.
- Use row numbers matching the input file: header is row `1`, first data row is row `2`.
- Read the first column as `query` and the second column as `input_answer`.
- Backfilled file preserves headers, row order, and extra columns; successful rows replace column 2 with the model answer.
- Failed rows keep the original second-column answer.
- Bad case JSONL contains only non-usable feedback submitted from batch rows.
- `usable` feedback may still call `/api/bad-cases`, but does not enter the downloaded bad case JSONL.

---

### Task 1: Batch Helper Tests

**Files:**
- Create: `web/src/batch.test.ts`
- Create: `web/src/batch.ts`

- [ ] **Step 1: Write failing parser and export tests**

Create `web/src/batch.test.ts`:

```ts
import {
  buildBadCaseJsonl,
  buildBackfilledDelimitedText,
  parseBatchDelimitedInput
} from "./batch";
import type { BatchBadCaseJsonlRecord, BatchQuestion } from "./types";

test("parses txt comma-separated table with header and answer column", () => {
  const result = parseBatchDelimitedInput({
    text: "问题,答案\n客户说每年不能超过80万怎么办？,\n保单整理有什么作用？,人工答案",
    sourceLabel: "questions.txt",
    sourceFormat: "txt"
  });

  expect(result.headers).toEqual(["问题", "答案"]);
  expect(result.rows).toEqual([
    ["客户说每年不能超过80万怎么办？", ""],
    ["保单整理有什么作用？", "人工答案"]
  ]);
  expect(result.questions).toMatchObject([
    {
      row_index: 2,
      query: "客户说每年不能超过80万怎么办？",
      input_answer: "",
      status: "pending"
    },
    {
      row_index: 3,
      query: "保单整理有什么作用？",
      input_answer: "人工答案",
      status: "pending"
    }
  ]);
});

test("parses quoted commas, quotes, and multiline fields", () => {
  const result = parseBatchDelimitedInput({
    text: '问题,答案\n"客户说预算, 不够怎么办？","旧答案"\n"第一行\n第二行","含 ""引号"""',
    sourceLabel: "questions.csv",
    sourceFormat: "csv"
  });

  expect(result.questions.map((item) => item.query)).toEqual([
    "客户说预算, 不够怎么办？",
    "第一行\n第二行"
  ]);
  expect(result.questions[1].input_answer).toBe('含 "引号"');
});

test("rejects input with fewer than two columns", () => {
  expect(() =>
    parseBatchDelimitedInput({
      text: "问题\n客户说每年不能超过80万怎么办？",
      sourceLabel: "bad.csv",
      sourceFormat: "csv"
    })
  ).toThrow("文件必须包含问题和答案两列");
});

test("rejects input with no executable questions", () => {
  expect(() =>
    parseBatchDelimitedInput({
      text: "问题,答案\n,",
      sourceLabel: "empty.csv",
      sourceFormat: "csv"
    })
  ).toThrow("没有解析到可执行的问题");
});

test("rejects more than one hundred questions", () => {
  const rows = Array.from({ length: 101 }, (_, index) => `问题${index + 1},`);

  expect(() =>
    parseBatchDelimitedInput({
      text: `问题,答案\n${rows.join("\n")}`,
      sourceLabel: "too-many.csv",
      sourceFormat: "csv"
    })
  ).toThrow("单批最多支持 100 个问题，请拆分后再运行");
});

test("builds backfilled delimited text while preserving failed rows and extra columns", () => {
  const questions = [
    {
      id: "q1",
      row_index: 2,
      query: "客户说每年不能超过80万怎么办？",
      input_answer: "",
      top_n: 20,
      top_k: 5,
      status: "succeeded",
      process_steps: [],
      streaming_answer: "模型答案1",
      response: {
        answer: "模型答案1",
        citations: [],
        evidence_count: 0
      }
    },
    {
      id: "q2",
      row_index: 3,
      query: "保单整理有什么作用？",
      input_answer: "原答案2",
      top_n: 20,
      top_k: 5,
      status: "failed",
      process_steps: [],
      streaming_answer: "",
      error: "问答服务暂时不可用"
    }
  ] satisfies BatchQuestion[];

  const text = buildBackfilledDelimitedText({
    headers: ["问题", "答案", "标签"],
    rows: [
      ["客户说每年不能超过80万怎么办？", "", "预算"],
      ["保单整理有什么作用？", "原答案2", "整理"]
    ],
    questions
  });

  expect(text).toBe(
    "问题,答案,标签\n客户说每年不能超过80万怎么办？,模型答案1,预算\n保单整理有什么作用？,原答案2,整理"
  );
});

test("builds bad case jsonl from non-usable batch feedback records", () => {
  const records = [
    {
      batch_source_label: "questions.csv",
      row_index: 2,
      input_answer: "人工答案",
      query: "客户说每年不能超过80万怎么办？",
      rewritten_query: "客户预算上限80万时如何回应",
      answer: "模型答案",
      top_n: 20,
      top_k: 5,
      feedback_result: "incomplete",
      problem_tags: ["missing_talk_track"],
      problem_detail: "缺话术",
      expected_answer: "应该补预算承接话术",
      reference_note: "案例A 第3节",
      evidence_feedback: [],
      issue_types: ["incomplete", "missing_talk_track"],
      expected_knowledge: "应该补预算承接话术",
      expected_source: "案例A 第3节",
      note: "缺话术",
      citations: [],
      retrieval_evidences: []
    }
  ] satisfies BatchBadCaseJsonlRecord[];

  const jsonl = buildBadCaseJsonl(records);

  expect(jsonl).toBe(`${JSON.stringify(records[0])}\n`);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd web && npm run test -- src/batch.test.ts
```

Expected: FAIL because `web/src/batch.ts` and the exported functions/types do not exist.

- [ ] **Step 3: Create minimal helper implementation**

Create `web/src/batch.ts`:

```ts
import type {
  BatchBadCaseJsonlRecord,
  BatchQuestion,
  BatchRunState,
  BatchSourceFormat
} from "./types";

const MAX_BATCH_QUESTIONS = 100;

type ParseBatchInput = {
  text: string;
  sourceLabel: string;
  sourceFormat: BatchSourceFormat;
};

export function parseBatchDelimitedInput({
  text,
  sourceLabel,
  sourceFormat
}: ParseBatchInput): BatchRunState {
  const table = parseCsv(text);
  const [headers, ...rawRows] = table;
  if (!headers || headers.length < 2) {
    throw new Error("文件必须包含问题和答案两列");
  }

  const rows = rawRows
    .filter((row) => row.some((cell) => cell.trim()))
    .map((row) => normalizeRowLength(row, headers.length));

  const questions = rows
    .map((row, index): BatchQuestion | null => {
      const query = (row[0] ?? "").trim();
      if (!query) {
        return null;
      }
      return {
        id: makeBatchQuestionId(index),
        row_index: index + 2,
        query,
        input_answer: (row[1] ?? "").trim(),
        top_n: 20,
        top_k: 5,
        status: "pending",
        process_steps: [],
        streaming_answer: ""
      };
    })
    .filter((item): item is BatchQuestion => item !== null);

  if (questions.length === 0) {
    throw new Error("没有解析到可执行的问题");
  }
  if (questions.length > MAX_BATCH_QUESTIONS) {
    throw new Error("单批最多支持 100 个问题，请拆分后再运行");
  }

  return {
    source_label: sourceLabel,
    source_format: sourceFormat,
    headers: normalizeRowLength(headers, Math.max(headers.length, 2)),
    rows,
    questions,
    running: false
  };
}

export function buildBackfilledDelimitedText({
  headers,
  rows,
  questions
}: {
  headers: string[];
  rows: string[][];
  questions: BatchQuestion[];
}): string {
  const answerByRowIndex = new Map(
    questions
      .filter((question) => question.status === "succeeded" && question.response)
      .map((question) => [question.row_index, question.response?.answer ?? ""])
  );
  const outputRows = rows.map((row, index) => {
    const rowIndex = index + 2;
    const nextRow = normalizeRowLength(row, headers.length);
    const answer = answerByRowIndex.get(rowIndex);
    if (answer !== undefined) {
      nextRow[1] = answer;
    }
    return nextRow;
  });

  return [headers, ...outputRows].map(serializeCsvRow).join("\n");
}

export function buildBadCaseJsonl(records: BatchBadCaseJsonlRecord[]): string {
  if (records.length === 0) {
    return "";
  }
  return `${records.map((record) => JSON.stringify(record)).join("\n")}\n`;
}

export function backfilledDownloadName(sourceLabel: string): string {
  const trimmed = sourceLabel.trim();
  if (!trimmed) {
    return "batch-answers.csv";
  }
  return trimmed.replace(/(\.csv|\.txt)?$/i, "-answered$1");
}

export function badCaseJsonlDownloadName(sourceLabel: string): string {
  const base = sourceLabel.trim().replace(/\.(csv|txt)$/i, "") || "batch";
  return `${base}-bad-cases.jsonl`;
}

function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const nextChar = text[index + 1];

    if (inQuotes) {
      if (char === '"' && nextChar === '"') {
        field += '"';
        index += 1;
        continue;
      }
      if (char === '"') {
        inQuotes = false;
        continue;
      }
      field += char;
      continue;
    }

    if (char === '"') {
      inQuotes = true;
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
      continue;
    }
    field += char;
  }

  row.push(field);
  if (row.length > 1 || row[0].trim()) {
    rows.push(row);
  }
  return rows;
}

function normalizeRowLength(row: string[], length: number): string[] {
  return Array.from({ length }, (_, index) => row[index] ?? "");
}

function serializeCsvRow(row: string[]): string {
  return row.map(serializeCsvCell).join(",");
}

function serializeCsvCell(value: string): string {
  if (!/[",\n\r]/.test(value)) {
    return value;
  }
  return `"${value.replace(/"/g, '""')}"`;
}

function makeBatchQuestionId(index: number): string {
  return `batch-question-${index + 1}`;
}
```

- [ ] **Step 4: Add batch types**

Modify `web/src/types.ts` after `AnswerStreamEvent`:

```ts
export type BatchSourceFormat = "txt" | "csv" | "pasted";

export type BatchQuestionStatus = "pending" | "running" | "succeeded" | "failed";

export type BatchBadCaseJsonlRecord = BadCaseRequest & {
  batch_source_label: string;
  row_index: number;
  input_answer: string;
};

export type BatchQuestion = {
  id: string;
  row_index: number;
  query: string;
  input_answer: string;
  top_n: number;
  top_k: number;
  status: BatchQuestionStatus;
  process_steps: AnswerProcessStep[];
  streaming_answer: string;
  response?: AnswerResponse;
  error?: string;
  bad_case_payload?: BatchBadCaseJsonlRecord;
};

export type BatchRunState = {
  source_label: string;
  source_format: BatchSourceFormat;
  headers: string[];
  rows: string[][];
  questions: BatchQuestion[];
  running: boolean;
  active_question_id?: string;
};
```

- [ ] **Step 5: Run helper tests to verify they pass**

Run:

```bash
cd web && npm run test -- src/batch.test.ts
```

Expected: PASS.

- [ ] **Step 6: Commit helper layer**

```bash
git add web/src/batch.ts web/src/batch.test.ts web/src/types.ts
git commit -m "feat: add batch qa parsing helpers"
```

---

### Task 2: Batch Mode UI Shell

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.test.tsx`
- Modify: `web/src/styles.css`

- [ ] **Step 1: Write failing UI shell test**

Append to `web/src/App.test.tsx`:

```ts
test("parses pasted comma-separated batch questions", async () => {
  const user = userEvent.setup();
  installFetchStub();
  render(<App />);

  await user.click(screen.getByRole("button", { name: "批量" }));
  await user.type(
    screen.getByLabelText("批量问题内容"),
    "问题,答案\n客户说每年不能超过80万怎么办？,\n保单整理有什么作用？,人工答案"
  );
  await user.click(screen.getByRole("button", { name: "解析内容" }));

  expect(await screen.findByText("已解析 2 个问题")).toBeInTheDocument();
  expect(screen.getByText("客户说每年不能超过80万怎么办？")).toBeInTheDocument();
  expect(screen.getByText("人工答案")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "开始批量运行" })).toBeEnabled();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd web && npm run test -- src/App.test.tsx -t "parses pasted comma-separated batch questions"
```

Expected: FAIL because the batch mode UI does not exist.

- [ ] **Step 3: Add state, imports, and mode switch**

Modify `web/src/App.tsx` imports:

```ts
import {
  badCaseJsonlDownloadName,
  backfilledDownloadName,
  buildBadCaseJsonl,
  buildBackfilledDelimitedText,
  parseBatchDelimitedInput
} from "./batch";
```

Extend the type import:

```ts
  BatchBadCaseJsonlRecord,
  BatchQuestion,
  BatchRunState,
  BatchSourceFormat,
```

Add near constants:

```ts
type WorkMode = "single" | "batch";
```

Inside `App()` add state after `topK`:

```ts
  const [workMode, setWorkMode] = useState<WorkMode>("single");
  const [batchText, setBatchText] = useState("");
  const [batchFileName, setBatchFileName] = useState("pasted.csv");
  const [batchState, setBatchState] = useState<BatchRunState | null>(null);
  const [batchError, setBatchError] = useState("");
```

Add mode switch in the panel header before the clear button:

```tsx
          <div className="mode-switch" role="group" aria-label="问答模式">
            <button
              type="button"
              className={workMode === "single" ? "mode-button selected" : "mode-button"}
              aria-pressed={workMode === "single"}
              onClick={() => setWorkMode("single")}
            >
              单问
            </button>
            <button
              type="button"
              className={workMode === "batch" ? "mode-button selected" : "mode-button"}
              aria-pressed={workMode === "batch"}
              onClick={() => setWorkMode("batch")}
            >
              批量
            </button>
          </div>
```

- [ ] **Step 4: Add parse handlers and batch shell JSX**

Add helper functions inside `App()` before `return`:

```ts
  function parseBatchTextInput() {
    try {
      const parsed = parseBatchDelimitedInput({
        text: batchText,
        sourceLabel: batchFileName,
        sourceFormat: sourceFormatFromName(batchFileName)
      });
      setBatchState(parsed);
      setBatchError("");
      setSelectedCitation(null);
      setRevealMessage("");
    } catch (error) {
      setBatchState(null);
      setBatchError(error instanceof Error ? error.message : "无法解析批量问题。");
    }
  }

  async function handleBatchFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    const format = sourceFormatFromName(file.name);
    if (format === "pasted") {
      setBatchError("仅支持 txt 或 csv 文件");
      return;
    }
    const text = await file.text();
    setBatchFileName(file.name);
    setBatchText(text);
    try {
      const parsed = parseBatchDelimitedInput({
        text,
        sourceLabel: file.name,
        sourceFormat: format
      });
      setBatchState(parsed);
      setBatchError("");
    } catch (error) {
      setBatchState(null);
      setBatchError(error instanceof Error ? error.message : "无法解析批量问题。");
    }
  }
```

In `web/src/App.tsx`, wrap the current single-question UI in a mode branch:

- The current `<section className="turn-list" aria-live="polite">` block stays unchanged and becomes the first child of the `workMode === "single"` branch.
- The current `<form className="question-form" onSubmit={handleSubmit}>` block stays unchanged and becomes the second child of the `workMode === "single"` branch.
- The `workMode === "batch"` branch renders `BatchPanel` with these props:

```tsx
          <BatchPanel
            batchText={batchText}
            batchState={batchState}
            batchError={batchError}
            running={Boolean(batchState?.running)}
            onTextChange={(value) => {
              setBatchText(value);
              setBatchError("");
            }}
            onParse={parseBatchTextInput}
            onFileChange={handleBatchFileChange}
          />
```

Add component after `ProcessTimeline`:

```tsx
function BatchPanel({
  batchText,
  batchState,
  batchError,
  running,
  onTextChange,
  onParse,
  onFileChange
}: {
  batchText: string;
  batchState: BatchRunState | null;
  batchError: string;
  running: boolean;
  onTextChange: (value: string) => void;
  onParse: () => void;
  onFileChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <section className="batch-panel" aria-label="批量跑问题">
      <div className="batch-inputs">
        <label className="file-field">
          <span>上传问题文件</span>
          <input
            aria-label="上传问题文件"
            type="file"
            accept=".txt,.csv"
            disabled={running}
            onChange={onFileChange}
          />
        </label>
        <label className="text-field">
          <span>批量问题内容</span>
          <textarea
            rows={7}
            value={batchText}
            disabled={running}
            onChange={(event) => onTextChange(event.target.value)}
            placeholder={"问题,答案\n客户说每年不能超过80万怎么办？,"}
          />
        </label>
        <div className="batch-actions">
          <button className="secondary-button compact-button" type="button" disabled={running} onClick={onParse}>
            解析内容
          </button>
        </div>
        {batchError && <p className="form-error">{batchError}</p>}
      </div>
      {batchState && (
        <div className="batch-results" aria-label="批量结果">
          <p className="meta-text">已解析 {batchState.questions.length} 个问题</p>
          {batchState.questions.map((question, index) => (
            <article className="batch-result-item" key={question.id}>
              <div className="batch-result-header">
                <strong>{index + 1}. {question.query}</strong>
                <span className={`status-chip ${question.status}`}>{batchStatusLabel(question.status)}</span>
              </div>
              {question.input_answer && (
                <p className="meta-text">原答案：{question.input_answer}</p>
              )}
            </article>
          ))}
          <button className="primary-button" type="button" disabled={running}>
            开始批量运行
          </button>
        </div>
      )}
    </section>
  );
}
```

Add helpers near formatting helpers:

```ts
function sourceFormatFromName(name: string): BatchSourceFormat {
  if (/\.txt$/i.test(name)) {
    return "txt";
  }
  if (/\.csv$/i.test(name)) {
    return "csv";
  }
  return name === "pasted.csv" ? "pasted" : "pasted";
}

function batchStatusLabel(status: BatchQuestion["status"]): string {
  return {
    pending: "等待中",
    running: "运行中",
    succeeded: "成功",
    failed: "失败"
  }[status];
}
```

- [ ] **Step 5: Add minimal styles**

Append to `web/src/styles.css` before media queries:

```css
.mode-switch {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: rgb(255 255 255 / 0.58);
  padding: 3px;
}

.mode-button {
  min-height: 34px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--color-muted);
  padding: 6px 12px;
  font-weight: 750;
}

.mode-button.selected {
  background: var(--color-accent-soft);
  color: var(--color-accent-hover);
  box-shadow: 0 8px 18px rgb(79 70 229 / 0.08);
}

.batch-panel {
  display: grid;
  gap: 16px;
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 20px;
}

.batch-inputs,
.batch-results {
  display: grid;
  gap: 12px;
}

.file-field,
.text-field {
  display: grid;
  gap: 7px;
}

.file-field span,
.text-field span {
  color: #485a72;
  font-size: 13px;
  font-weight: 700;
}

.batch-actions,
.batch-result-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.batch-result-item {
  display: grid;
  gap: 10px;
  border: 1px solid rgb(226 232 240 / 0.74);
  border-radius: 14px;
  background: rgb(255 255 255 / 0.72);
  padding: 13px;
}

.batch-result-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.batch-result-header strong {
  overflow-wrap: anywhere;
}

.status-chip {
  flex: 0 0 auto;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  padding: 3px 9px;
  color: var(--color-muted);
  font-size: 12px;
  font-weight: 750;
}

.status-chip.running {
  border-color: rgb(79 70 229 / 0.28);
  background: var(--color-accent-soft);
  color: var(--color-accent-hover);
}

.status-chip.succeeded {
  border-color: rgb(20 122 85 / 0.26);
  background: rgb(236 253 245 / 0.78);
  color: var(--color-success);
}

.status-chip.failed {
  border-color: rgb(180 35 53 / 0.28);
  background: rgb(255 245 245 / 0.86);
  color: var(--color-error);
}
```

- [ ] **Step 6: Run UI shell test**

Run:

```bash
cd web && npm run test -- src/App.test.tsx -t "parses pasted comma-separated batch questions"
```

Expected: PASS.

- [ ] **Step 7: Commit UI shell**

```bash
git add web/src/App.tsx web/src/App.test.tsx web/src/styles.css
git commit -m "feat: add batch qa input shell"
```

---

### Task 3: Serial Batch Execution

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.test.tsx`

- [ ] **Step 1: Write failing serial execution test**

Append to `web/src/App.test.tsx`:

```ts
test("runs batch questions sequentially and shows per-row process state", async () => {
  const user = userEvent.setup();
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
    if (url.endsWith("/api/answer/stream")) {
      const body = JSON.parse(String(init?.body ?? "{}"));
      return sseResponse([
        {
          event: "step",
          data: {
            type: "step",
            step: "search.query_understood",
            message: "已完成问题理解",
            payload: { rewritten_query: `${body.query} 改写` }
          }
        },
        {
          event: "final",
          data: {
            type: "final",
            response: {
              ...answerPayload,
              answer: `${body.query} 的模型答案`,
              rewritten_query: `${body.query} 改写`
            }
          }
        }
      ]);
    }
    return jsonResponse({ detail: "not found" }, { status: 404 });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);

  await user.click(screen.getByRole("button", { name: "批量" }));
  await user.type(
    screen.getByLabelText("批量问题内容"),
    "问题,答案\n客户说每年不能超过80万怎么办？,\n保单整理有什么作用？,"
  );
  await user.click(screen.getByRole("button", { name: "解析内容" }));
  await user.click(await screen.findByRole("button", { name: "开始批量运行" }));

  expect(
    await screen.findByText("客户说每年不能超过80万怎么办？ 的模型答案")
  ).toBeInTheDocument();
  expect(screen.getByText("保单整理有什么作用？ 的模型答案")).toBeInTheDocument();
  expect(screen.getAllByText("已完成问题理解")).toHaveLength(2);
  expect(requests.filter((request) => request.url === "/api/answer/stream").map((request) => request.body)).toEqual([
    { query: "客户说每年不能超过80万怎么办？", top_n: 20, top_k: 5 },
    { query: "保单整理有什么作用？", top_n: 20, top_k: 5 }
  ]);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd web && npm run test -- src/App.test.tsx -t "runs batch questions sequentially"
```

Expected: FAIL because the start button does not run anything yet.

- [ ] **Step 3: Add batch update helpers**

Add inside `App()`:

```ts
  function updateBatchQuestion(
    questionId: string,
    updater: (question: BatchQuestion) => BatchQuestion
  ) {
    setBatchState((current) => {
      if (!current) {
        return current;
      }
      return {
        ...current,
        questions: current.questions.map((question) =>
          question.id === questionId ? updater(question) : question
        )
      };
    });
  }

  function setBatchRunning(running: boolean, activeQuestionId?: string) {
    setBatchState((current) =>
      current
        ? {
            ...current,
            running,
            active_question_id: activeQuestionId
          }
        : current
    );
  }
```

- [ ] **Step 4: Add serial runner**

Add inside `App()`:

```ts
  async function runBatch() {
    if (!batchState || batchState.running) {
      return;
    }
    const limitError = validateLimits(topN, topK);
    if (limitError) {
      setBatchError(limitError);
      return;
    }

    setBatchError("");
    setBatchRunning(true);
    for (const question of batchState.questions) {
      await runBatchQuestion(question.id, topN, topK);
    }
    setBatchRunning(false);
  }

  async function retryBatchQuestion(questionId: string) {
    if (!batchState || batchState.running) {
      return;
    }
    const limitError = validateLimits(topN, topK);
    if (limitError) {
      setBatchError(limitError);
      return;
    }
    setBatchError("");
    setBatchRunning(true, questionId);
    await runBatchQuestion(questionId, topN, topK);
    setBatchRunning(false);
  }

  async function runBatchQuestion(questionId: string, submittedTopN: number, submittedTopK: number) {
    const question = batchState?.questions.find((item) => item.id === questionId);
    if (!question) {
      return;
    }
    updateBatchQuestion(questionId, (item) => ({
      ...item,
      top_n: submittedTopN,
      top_k: submittedTopK,
      status: "running",
      process_steps: [],
      streaming_answer: "",
      response: undefined,
      error: undefined
    }));
    setBatchRunning(true, questionId);

    try {
      const response = await answerQuestionStream(
        {
          query: question.query,
          top_n: submittedTopN,
          top_k: submittedTopK
        },
        {
          onEvent: (event) => {
            if (event.type === "step") {
              updateBatchQuestion(questionId, (item) => ({
                ...item,
                process_steps: [
                  ...item.process_steps,
                  {
                    step: event.step,
                    message: event.message,
                    payload: event.payload
                  }
                ]
              }));
            }
            if (event.type === "answer_delta") {
              updateBatchQuestion(questionId, (item) => ({
                ...item,
                streaming_answer: `${item.streaming_answer}${event.text}`
              }));
            }
            if (event.type === "final") {
              updateBatchQuestion(questionId, (item) => ({
                ...item,
                status: "succeeded",
                response: event.response,
                streaming_answer: event.response.answer
              }));
            }
          }
        }
      );
      updateBatchQuestion(questionId, (item) => ({
        ...item,
        status: "succeeded",
        response,
        streaming_answer: response.answer
      }));
    } catch (error) {
      const message = error instanceof Error ? error.message : "问答失败";
      updateBatchQuestion(questionId, (item) => ({
        ...item,
        status: "failed",
        error: message
      }));
    }
  }
```

- [ ] **Step 5: Render batch result content and retry**

Extend `BatchPanel` props with:

```ts
  onRun: () => void;
  onRetry: (questionId: string) => void;
  onSelectCitation: (citation: Citation) => void;
```

Pass from `App`:

```tsx
            onRun={() => void runBatch()}
            onRetry={(questionId) => void retryBatchQuestion(questionId)}
            onSelectCitation={(citation) => {
              setSelectedCitation(citation);
              setRevealMessage("");
            }}
```

Replace the start button:

```tsx
          <button
            className="primary-button"
            type="button"
            disabled={running}
            onClick={onRun}
          >
            {running ? (
              <LoaderCircle className="spin" size={18} aria-hidden="true" />
            ) : (
              <Send size={18} aria-hidden="true" />
            )}
            开始批量运行
          </button>
```

Inside each `batch-result-item`, render:

```tsx
              {question.status === "running" && (
                <ProcessTimeline active steps={question.process_steps} />
              )}
              {question.process_steps.length > 0 && question.status !== "running" && (
                <ProcessTimeline active={false} steps={question.process_steps} />
              )}
              {(question.streaming_answer || question.response?.answer) && (
                <p>{question.response?.answer || question.streaming_answer}</p>
              )}
              {question.response && (
                <>
                  <CitationList
                    citations={question.response.citations}
                    selectedCitation={null}
                    onSelect={onSelectCitation}
                  />
                  <BadCasePanel
                    turn={{
                      id: question.id,
                      query: question.query,
                      top_n: question.top_n,
                      top_k: question.top_k
                    }}
                    response={question.response}
                  />
                </>
              )}
              {question.error && (
                <div className="error-message batch-error-row">
                  <p>{question.error}</p>
                  <button
                    className="inline-button"
                    type="button"
                    disabled={running}
                    onClick={() => onRetry(question.id)}
                  >
                    <RefreshCcw size={16} aria-hidden="true" />
                    重试
                  </button>
                </div>
              )}
```

- [ ] **Step 6: Run serial execution test**

Run:

```bash
cd web && npm run test -- src/App.test.tsx -t "runs batch questions sequentially"
```

Expected: PASS.

- [ ] **Step 7: Commit serial runner**

```bash
git add web/src/App.tsx web/src/App.test.tsx
git commit -m "feat: run batch qa rows serially"
```

---

### Task 4: Failure Isolation And Retry

**Files:**
- Modify: `web/src/App.test.tsx`
- Modify: `web/src/App.tsx`

- [ ] **Step 1: Write failing failure isolation test**

Append to `web/src/App.test.tsx`:

```ts
test("continues batch execution after one row fails and retries the failed row", async () => {
  const user = userEvent.setup();
  let firstQuestionAttempts = 0;
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/status")) {
      return jsonResponse(statusPayload);
    }
    if (url.endsWith("/api/answer/stream")) {
      const body = JSON.parse(String(init?.body ?? "{}"));
      if (body.query === "失败问题" && firstQuestionAttempts === 0) {
        firstQuestionAttempts += 1;
        return sseResponse([
          { event: "error", data: { type: "error", detail: "问答服务暂时不可用" } }
        ]);
      }
      return sseResponse([
        {
          event: "final",
          data: {
            type: "final",
            response: {
              ...answerPayload,
              answer: `${body.query} 成功答案`,
              rewritten_query: `${body.query} 改写`
            }
          }
        }
      ]);
    }
    return jsonResponse({ detail: "not found" }, { status: 404 });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);

  await user.click(screen.getByRole("button", { name: "批量" }));
  await user.type(screen.getByLabelText("批量问题内容"), "问题,答案\n失败问题,\n后续问题,");
  await user.click(screen.getByRole("button", { name: "解析内容" }));
  await user.click(await screen.findByRole("button", { name: "开始批量运行" }));

  expect(await screen.findByText("问答服务暂时不可用")).toBeInTheDocument();
  expect(screen.getByText("后续问题 成功答案")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "重试" }));

  expect(await screen.findByText("失败问题 成功答案")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify failure mode**

Run:

```bash
cd web && npm run test -- src/App.test.tsx -t "continues batch execution"
```

Expected: PASS if Task 3 catch-and-continue was implemented correctly; if it fails, fix `runBatch()` so it always continues after `runBatchQuestion()`.

- [ ] **Step 3: Ensure running state is cleared after retry**

If the test hangs or buttons remain disabled, change `retryBatchQuestion()` to use `try/finally`:

```ts
    setBatchRunning(true, questionId);
    try {
      await runBatchQuestion(questionId, topN, topK);
    } finally {
      setBatchRunning(false);
    }
```

Apply the same `try/finally` pattern to `runBatch()`.

- [ ] **Step 4: Run failure test again**

Run:

```bash
cd web && npm run test -- src/App.test.tsx -t "continues batch execution"
```

Expected: PASS.

- [ ] **Step 5: Commit failure isolation**

```bash
git add web/src/App.tsx web/src/App.test.tsx
git commit -m "feat: isolate batch qa row failures"
```

---

### Task 5: Backfilled Download

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.test.tsx`

- [ ] **Step 1: Write failing download test**

Append to `web/src/App.test.tsx`:

```ts
test("downloads a backfilled comma-separated answer file", async () => {
  const user = userEvent.setup();
  const { textParts, restore } = installDownloadStub();
  installFetchStub();
  render(<App />);

  await user.click(screen.getByRole("button", { name: "批量" }));
  await user.type(
    screen.getByLabelText("批量问题内容"),
    "问题,答案,标签\n客户说每年不能超过80万怎么办？,,预算"
  );
  await user.click(screen.getByRole("button", { name: "解析内容" }));
  await user.click(await screen.findByRole("button", { name: "开始批量运行" }));
  expect(await screen.findByText("先承接预算，再讨论缴费期和保障缺口。")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "下载回填文件" }));

  expect(textParts.join("")).toContain("问题,答案,标签");
  expect(textParts.join("")).toContain("客户说每年不能超过80万怎么办？,先承接预算，再讨论缴费期和保障缺口。,预算");
  restore();
});
```

Add helper near `installStorageStub()`:

```ts
function installDownloadStub() {
  const textParts: string[] = [];
  const originalCreateElement = document.createElement.bind(document);
  const click = vi.fn();
  vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => {
    if (blob instanceof Blob) {
      void blob.text().then((text) => textParts.push(text));
    }
    return "blob:batch-download";
  });
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  vi.spyOn(document, "createElement").mockImplementation((tagName: string) => {
    const element = originalCreateElement(tagName);
    if (tagName.toLowerCase() === "a") {
      Object.defineProperty(element, "click", { value: click });
    }
    return element;
  });
  return {
    textParts,
    restore: () => {
      vi.restoreAllMocks();
    }
  };
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd web && npm run test -- src/App.test.tsx -t "downloads a backfilled"
```

Expected: FAIL because download button and handler do not exist.

- [ ] **Step 3: Add download helper and handler**

Add outside `App()`:

```ts
function downloadTextFile(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
```

Add inside `App()`:

```ts
  function downloadBackfilledFile() {
    if (!batchState) {
      return;
    }
    const text = buildBackfilledDelimitedText({
      headers: batchState.headers,
      rows: batchState.rows,
      questions: batchState.questions
    });
    downloadTextFile(backfilledDownloadName(batchState.source_label), text);
  }
```

Pass `onDownloadBackfilled={downloadBackfilledFile}` to `BatchPanel`, and add prop:

```ts
  onDownloadBackfilled: () => void;
```

Render button in batch actions:

```tsx
          <button
            className="secondary-button compact-button"
            type="button"
            disabled={running || !batchState.questions.some((question) => question.status === "succeeded")}
            onClick={onDownloadBackfilled}
          >
            下载回填文件
          </button>
```

- [ ] **Step 4: Run download test**

Run:

```bash
cd web && npm run test -- src/App.test.tsx -t "downloads a backfilled"
```

Expected: PASS.

- [ ] **Step 5: Commit backfilled download**

```bash
git add web/src/App.tsx web/src/App.test.tsx
git commit -m "feat: export backfilled batch answers"
```

---

### Task 6: Batch Bad Case JSONL

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.test.tsx`

- [ ] **Step 1: Write failing bad case JSONL test**

Append to `web/src/App.test.tsx`:

```ts
test("exports bad case jsonl only for non-usable batch feedback", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub();
  const { textParts, restore } = installDownloadStub();
  render(<App />);

  await user.click(screen.getByRole("button", { name: "批量" }));
  await user.type(
    screen.getByLabelText("批量问题内容"),
    "问题,答案\n客户说每年不能超过80万怎么办？,人工答案"
  );
  await user.click(screen.getByRole("button", { name: "解析内容" }));
  await user.click(await screen.findByRole("button", { name: "开始批量运行" }));
  await user.click(await screen.findByRole("button", { name: "不完整" }));
  await user.click(screen.getByLabelText("缺关键话术"));
  await user.type(screen.getByLabelText("哪里不对"), "当前回答没有讲清楚保障缺口。");
  await user.type(screen.getByLabelText("正确回答应包含什么"), "应该命中保障缺口分析。");
  await user.click(screen.getByRole("button", { name: "保存反馈" }));

  expect(await screen.findByText("反馈已保存。")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "下载 bad case JSONL" }));

  const jsonl = textParts.join("");
  const record = JSON.parse(jsonl.trim());
  expect(record).toMatchObject({
    batch_source_label: "pasted.csv",
    row_index: 2,
    input_answer: "人工答案",
    query: "客户说每年不能超过80万怎么办？",
    answer: "先承接预算，再讨论缴费期和保障缺口。",
    feedback_result: "incomplete",
    problem_tags: ["missing_talk_track"]
  });
  expect(requests).toContainEqual(
    expect.objectContaining({
      url: "/api/bad-cases",
      body: expect.objectContaining({
        query: "客户说每年不能超过80万怎么办？",
        feedback_result: "incomplete"
      })
    })
  );
  restore();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd web && npm run test -- src/App.test.tsx -t "exports bad case jsonl"
```

Expected: FAIL because `BadCasePanel` does not expose saved payloads to batch state and no bad case JSONL button exists.

- [ ] **Step 3: Modify BadCasePanel to report saved non-usable payloads**

Change `BadCasePanel` signature:

```ts
function BadCasePanel({
  turn,
  response,
  onSavedBadCase
}: {
  turn: ChatTurn;
  response: AnswerResponse;
  onSavedBadCase?: (payload: BadCaseRequest) => void;
}) {
```

Add `BadCaseRequest` to type imports from `./types`.

In `saveFeedback()`, build the payload before calling `submitBadCase`:

```ts
      const payload: BadCaseRequest = {
        query: turn.query,
        rewritten_query: response.rewritten_query ?? "",
        answer: response.answer,
        top_n: turn.top_n,
        top_k: turn.top_k,
        feedback_result: feedbackResult,
        problem_tags: draft.problemTags,
        problem_detail: draft.problemDetail,
        expected_answer: draft.expectedAnswer,
        reference_note: draft.referenceNote,
        evidence_feedback: draft.evidenceFeedback,
        issue_types: issueTypes,
        expected_knowledge: draft.expectedAnswer,
        expected_source: draft.referenceNote,
        note: draft.problemDetail,
        citations: response.citations,
        retrieval_evidences: evidences
      };
      await submitBadCase(payload);
      if (feedbackResult !== "usable") {
        onSavedBadCase?.(payload);
      }
```

Remove the inline object currently passed directly to `submitBadCase`.

- [ ] **Step 4: Store batch bad case payload**

When rendering batch `BadCasePanel`, pass:

```tsx
                    onSavedBadCase={(payload) => {
                      onSavedBadCase(question, payload);
                    }}
```

Extend `BatchPanel` props:

```ts
  onSavedBadCase: (question: BatchQuestion, payload: BadCaseRequest) => void;
```

In `App()`, pass:

```tsx
            onSavedBadCase={(question, payload) => {
              updateBatchQuestion(question.id, (item) => ({
                ...item,
                bad_case_payload: {
                  ...payload,
                  batch_source_label: batchState?.source_label ?? "",
                  row_index: item.row_index,
                  input_answer: item.input_answer
                }
              }));
            }}
```

- [ ] **Step 5: Add bad case JSONL download**

Inside `App()`:

```ts
  function batchBadCaseRecords(): BatchBadCaseJsonlRecord[] {
    return batchState?.questions
      .map((question) => question.bad_case_payload)
      .filter((payload): payload is BatchBadCaseJsonlRecord => Boolean(payload)) ?? [];
  }

  function downloadBadCaseJsonl() {
    if (!batchState) {
      return;
    }
    downloadTextFile(
      badCaseJsonlDownloadName(batchState.source_label),
      buildBadCaseJsonl(batchBadCaseRecords())
    );
  }
```

Pass `badCaseCount={batchBadCaseRecords().length}` and `onDownloadBadCases={downloadBadCaseJsonl}` to `BatchPanel`.

Render button:

```tsx
          <button
            className="secondary-button compact-button"
            type="button"
            disabled={running || badCaseCount === 0}
            onClick={onDownloadBadCases}
          >
            下载 bad case JSONL
          </button>
```

- [ ] **Step 6: Run bad case JSONL test**

Run:

```bash
cd web && npm run test -- src/App.test.tsx -t "exports bad case jsonl"
```

Expected: PASS.

- [ ] **Step 7: Commit bad case JSONL export**

```bash
git add web/src/App.tsx web/src/App.test.tsx
git commit -m "feat: export batch bad case jsonl"
```

---

### Task 7: Full Verification And Build

**Files:**
- Verify only.

- [ ] **Step 1: Run focused frontend tests**

Run:

```bash
cd web && npm run test -- src/batch.test.ts src/App.test.tsx src/api.test.ts
```

Expected: PASS.

- [ ] **Step 2: Run full frontend test suite**

Run:

```bash
cd web && npm run test
```

Expected: PASS.

- [ ] **Step 3: Run frontend build**

Run:

```bash
cd web && npm run build
```

Expected: PASS with TypeScript and Vite build succeeding.

- [ ] **Step 4: Run Python web tests**

Run:

```bash
uv run pytest tests/test_web_app.py tests/test_web_services.py tests/test_web_bad_cases.py
```

Expected: PASS.

- [ ] **Step 5: Run full Python tests**

Run:

```bash
uv run pytest
```

Expected: PASS.

- [ ] **Step 6: Inspect git diff**

Run:

```bash
git status --short
git diff --stat
```

Expected: Only intentional changes in `web/src/batch.ts`, `web/src/batch.test.ts`, `web/src/types.ts`, `web/src/App.tsx`, `web/src/App.test.tsx`, and `web/src/styles.css`, plus any pre-existing unrelated user changes still present.
