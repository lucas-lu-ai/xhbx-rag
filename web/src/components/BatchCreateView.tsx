import { LoaderCircle } from "lucide-react";
import { type ChangeEvent, useState } from "react";
import { readSheet } from "read-excel-file/universal";
import writeExcelFile from "write-excel-file/universal";

import { createBatchRun } from "../api";
import {
  BATCH_TEMPLATE_FILE_NAME,
  BATCH_TEMPLATE_TABLE,
  buildBackfilledDelimitedText,
  parseBatchDelimitedInput,
  parseBatchTableInput
} from "../batch";
import { buildCreateBatchRunRequest } from "../batchRuns";
import { downloadBlobFile } from "../downloads";
import type {
  BatchRunState,
  BatchRunSummary,
  BatchSourceFormat
} from "../types";

type BatchCreateViewProps = {
  onCreated: (summary: BatchRunSummary) => void;
  topN: number;
  topK: number;
};

export function BatchCreateView({ onCreated, topN, topK }: BatchCreateViewProps) {
  const [batchText, setBatchText] = useState("");
  const [batchState, setBatchState] = useState<BatchRunState | null>(null);
  const [parseError, setParseError] = useState("");
  const [sourceLabel, setSourceLabel] = useState("pasted");
  const [sourceFormat, setSourceFormat] = useState<BatchSourceFormat>("pasted");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  function parseBatchText(
    text: string,
    nextSourceLabel: string,
    nextSourceFormat: BatchSourceFormat
  ) {
    try {
      setBatchState(
        parseBatchDelimitedInput({
          text,
          sourceLabel: nextSourceLabel,
          sourceFormat: nextSourceFormat,
          topN,
          topK
        })
      );
      setParseError("");
    } catch (error) {
      setBatchState(null);
      setParseError(error instanceof Error ? error.message : "解析失败");
    }
  }

  function handleTextChange(event: ChangeEvent<HTMLTextAreaElement>) {
    setBatchText(event.target.value);
    setSourceLabel("pasted");
    setSourceFormat("pasted");
    setBatchState(null);
    setParseError("");
    setCreateError("");
  }

  function handleParseClick() {
    parseBatchText(batchText, sourceLabel, sourceFormat);
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const input = event.target;
    const file = input.files?.[0];
    // 立即清空 input 值，允许用户重新选择同一个文件时仍能触发 change。
    input.value = "";
    if (!file) {
      return;
    }

    const nextSourceFormat = batchSourceFormatForFile(file.name);
    if (!nextSourceFormat) {
      setBatchState(null);
      setParseError("仅支持 txt、csv 或 xlsx 文件");
      return;
    }

    try {
      if (nextSourceFormat === "xlsx") {
        const tableRows = await readSheet(file);
        const parsed = parseBatchTableInput({
          rows: tableRows,
          sourceLabel: file.name,
          sourceFormat: nextSourceFormat,
          topN,
          topK
        });
        setBatchText(buildBackfilledDelimitedText(parsed));
        setSourceLabel(file.name);
        setSourceFormat(nextSourceFormat);
        setBatchState(parsed);
        setParseError("");
        return;
      }

      const text = await file.text();
      setBatchText(text);
      setSourceLabel(file.name);
      setSourceFormat(nextSourceFormat);
      parseBatchText(text, file.name, nextSourceFormat);
    } catch {
      setBatchState(null);
      setParseError("无法读取文件");
    }
  }

  async function downloadBatchTemplate() {
    const blob = await writeExcelFile([...BATCH_TEMPLATE_TABLE]).toBlob();
    downloadBlobFile(BATCH_TEMPLATE_FILE_NAME, blob);
  }

  async function handleCreate() {
    if (!batchState || creating) {
      return;
    }

    setCreating(true);
    setCreateError("");
    try {
      const summary = await createBatchRun(buildCreateBatchRunRequest(batchState));
      onCreated(summary);
    } catch (error) {
      setCreateError(
        error instanceof Error ? error.message : "无法创建批量任务"
      );
    } finally {
      setCreating(false);
    }
  }

  return (
    <section className="batch-panel" aria-label="新建批量执行">
      <div className="batch-inputs">
        <div className="file-field">
          <div className="file-field-heading">
            <label htmlFor="batch-file-input">上传批量文件</label>
            <button
              className="inline-button compact-button"
              type="button"
              onClick={() => void downloadBatchTemplate()}
            >
              下载 xlsx 模板
            </button>
          </div>
          <input
            id="batch-file-input"
            type="file"
            accept=".txt,.csv,.xlsx"
            onChange={(event) => void handleFileChange(event)}
          />
        </div>
        <label className="text-field batch-text-field" htmlFor="batch-content">
          <span>批量问题内容</span>
          <textarea
            id="batch-content"
            rows={8}
            value={batchText}
            onChange={handleTextChange}
            placeholder="问题,答案&#10;客户说每年不能超过80万怎么办？,人工答案"
          />
        </label>
      </div>

      <div className="batch-actions">
        <button
          className="secondary-button compact-button"
          type="button"
          onClick={handleParseClick}
        >
          解析内容
        </button>
        {batchState && (
          <span className="status-chip">已解析 {batchState.questions.length} 个问题</span>
        )}
      </div>

      {parseError && (
        <p className="form-error" role="alert">
          {parseError}
        </p>
      )}

      <div className="batch-results" aria-live="polite">
        {!batchState ? (
          <div className="batch-empty-state">
            <h2>等待解析</h2>
            <p>粘贴带表头的逗号分隔内容，或上传 txt/csv/xlsx 文件。</p>
          </div>
        ) : (
          <ol className="batch-result-list">
            {batchState.questions.map((question, index) => (
              <li key={question.id}>
                <article className="batch-result-item">
                  <div className="batch-result-heading">
                    <strong>问题 {index + 1}</strong>
                    <div className="batch-actions">
                      <span className="status-chip muted">
                        第 {question.row_index} 行
                      </span>
                    </div>
                  </div>
                  <p>{question.query}</p>
                  <div className="batch-original-answer">
                    <span>原答案</span>
                    <p>{question.input_answer.trim() || "未提供"}</p>
                  </div>
                </article>
              </li>
            ))}
          </ol>
        )}
      </div>

      <div className="batch-footer">
        {createError && (
          <p className="form-error" role="alert">
            {createError}
          </p>
        )}
        <button
          className="primary-button"
          type="button"
          disabled={!batchState || creating}
          onClick={() => void handleCreate()}
        >
          {creating && (
            <LoaderCircle className="spin" size={18} aria-hidden="true" />
          )}
          开始批量运行
        </button>
      </div>
    </section>
  );
}

function batchSourceFormatForFile(fileName: string): BatchSourceFormat | null {
  const normalized = fileName.toLowerCase();
  if (normalized.endsWith(".csv")) {
    return "csv";
  }
  if (normalized.endsWith(".xlsx")) {
    return "xlsx";
  }
  if (normalized.endsWith(".txt")) {
    return "txt";
  }
  return null;
}
