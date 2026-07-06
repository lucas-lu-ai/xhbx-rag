import { LoaderCircle, Play, RefreshCcw } from "lucide-react";
import { useState } from "react";
import writeExcelFile from "write-excel-file/universal";

import {
  getBatchRunDetail,
  resumeBatchRun,
  retryBatchRow,
  saveBatchRowBadCase
} from "../api";
import {
  backfilledDownloadName,
  badCaseJsonlDownloadName,
  buildBackfilledDelimitedText,
  buildBackfilledTable,
  buildBadCaseJsonl
} from "../batch";
import {
  batchBadCaseSourceLabel,
  batchQuestionDetailToChatTurn,
  batchQuestionStatusLabel,
  batchRunDetailToRunState,
  batchRunStatusLabel
} from "../batchRuns";
import { downloadBlobFile, downloadTextFile } from "../downloads";
import {
  POLL_STOPPED_MESSAGE,
  useBatchRunPolling
} from "../hooks/useBatchRunPolling";
import type {
  AnswerResponse,
  BadCaseRequest,
  BatchRunDetail,
  BatchRunQuestionDetail,
  Citation
} from "../types";
import { BadCasePanel } from "./BadCasePanel";

type BatchRunViewProps = {
  runId: string;
  pollIntervalMs?: number;
  selectedCitationKey: string | null;
  onSelectCitation: (
    citation: Citation,
    key: string,
    response: AnswerResponse
  ) => void;
  onRunMutated?: () => void;
};

export function BatchRunView({
  runId,
  pollIntervalMs,
  selectedCitationKey,
  onSelectCitation,
  onRunMutated
}: BatchRunViewProps) {
  const { detail, loadError, pollError, refresh, patchDetail } =
    useBatchRunPolling(runId, { intervalMs: pollIntervalMs });
  const [actionError, setActionError] = useState("");
  const [resuming, setResuming] = useState(false);
  const [exporting, setExporting] = useState(false);

  async function handleRetry(rowIndex: number) {
    setActionError("");
    try {
      await retryBatchRow(runId, rowIndex);
      refresh();
      onRunMutated?.();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "重试失败");
    }
  }

  async function handleResume() {
    if (resuming) {
      return;
    }
    setActionError("");
    setResuming(true);
    try {
      await resumeBatchRun(runId);
      refresh();
      onRunMutated?.();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "继续执行失败");
    } finally {
      setResuming(false);
    }
  }

  // 导出前用 include_table=true 拉一次完整详情，交给 batch.ts 的纯函数生成文件。
  async function exportWithTable(
    exporter: (detailWithTable: BatchRunDetail) => Promise<void>
  ) {
    if (exporting) {
      return;
    }
    setActionError("");
    setExporting(true);
    try {
      const detailWithTable = await getBatchRunDetail(runId, {
        includeTable: true
      });
      await exporter(detailWithTable);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }

  function downloadBackfilledFile() {
    return exportWithTable(async (detailWithTable) => {
      const state = batchRunDetailToRunState(detailWithTable);
      const table = buildBackfilledTable(state);
      const fileName = backfilledDownloadName(state.source_label);
      if (state.source_format === "xlsx") {
        const blob = await writeExcelFile(table).toBlob();
        downloadBlobFile(fileName, blob);
        return;
      }
      downloadTextFile(fileName, buildBackfilledDelimitedText(state));
    });
  }

  function downloadBadCaseJsonl() {
    return exportWithTable(async (detailWithTable) => {
      const state = batchRunDetailToRunState(detailWithTable);
      const records = state.questions
        .map((question) => question.bad_case_payload)
        .filter((payload) => payload !== undefined);
      downloadTextFile(
        badCaseJsonlDownloadName(state.source_label),
        buildBadCaseJsonl(records)
      );
    });
  }

  function submitRowBadCase(question: BatchRunQuestionDetail) {
    return async (payload: BadCaseRequest) => {
      if (!detail) {
        throw new Error("批量会话详情尚未加载");
      }
      const record = {
        ...payload,
        input_answer: question.input_answer,
        batch_source_label: batchBadCaseSourceLabel(detail)
      };
      const result = await saveBatchRowBadCase(runId, question.row_index, record);
      // 保存成功后本地同步该行 bad_case，避免等待下一次详情刷新。
      patchDetail((current) => ({
        ...current,
        questions: current.questions.map((item) =>
          item.row_index === question.row_index
            ? {
                ...item,
                bad_case: {
                  ...record,
                  bad_case_id: result.bad_case_id,
                  run_id: runId,
                  row_index: question.row_index
                }
              }
            : item
        )
      }));
      return result;
    };
  }

  if (!detail) {
    return (
      <section className="batch-panel" aria-label="批量会话">
        {loadError ? (
          <p className="form-error" role="alert">
            {loadError}
          </p>
        ) : (
          <div className="batch-empty-state">
            <h2>正在加载批量会话</h2>
            <p>正在获取批量执行详情...</p>
          </div>
        )}
      </section>
    );
  }

  const hasSucceededRow = detail.questions.some(
    (question) => question.status === "succeeded"
  );
  // 仅非 usable 的反馈会进入 bad case JSONL，usable 反馈不应点亮导出按钮。
  const hasExportableBadCase = detail.questions.some((question) => {
    const feedbackResult = (question.bad_case as { feedback_result?: string } | null)
      ?.feedback_result;
    return Boolean(question.bad_case) && feedbackResult !== "usable";
  });

  return (
    <section className="batch-panel" aria-label="批量会话">
      <div className="batch-run-header">
        <div className="batch-run-heading">
          <strong>{detail.title || detail.source_label}</strong>
          <span className="status-chip">{batchRunStatusLabel(detail.status)}</span>
        </div>
        <div className="batch-run-progress">
          <span>总数 {detail.question_total}</span>
          <span>完成 {detail.question_done}</span>
          <span>失败 {detail.question_failed}</span>
        </div>
        {detail.status === "interrupted" && (
          <button
            className="secondary-button compact-button"
            type="button"
            disabled={resuming}
            onClick={() => void handleResume()}
          >
            <Play size={16} aria-hidden="true" />
            继续执行
          </button>
        )}
        {pollError && (
          <div className="batch-poll-error">
            <p className="meta-text">{pollError}</p>
            {pollError === POLL_STOPPED_MESSAGE && (
              <button
                className="inline-button compact-button"
                type="button"
                onClick={() => refresh()}
              >
                <RefreshCcw size={14} aria-hidden="true" />
                手动刷新
              </button>
            )}
          </div>
        )}
        {actionError && (
          <p className="form-error" role="alert">
            {actionError}
          </p>
        )}
      </div>

      <ol className="batch-result-list">
        {detail.questions.map((question, index) => (
          <li key={`row-${question.row_index}`}>
            <BatchRunRow
              index={index}
              question={question}
              selectedCitationKey={selectedCitationKey}
              onSelectCitation={onSelectCitation}
              onRetry={() => void handleRetry(question.row_index)}
              submitBadCase={submitRowBadCase(question)}
            />
          </li>
        ))}
      </ol>

      <div className="batch-footer">
        <button
          className="secondary-button compact-button"
          type="button"
          disabled={exporting || !hasSucceededRow}
          onClick={() => void downloadBackfilledFile()}
        >
          下载回填文件
        </button>
        <button
          className="secondary-button compact-button"
          type="button"
          disabled={exporting || !hasExportableBadCase}
          onClick={() => void downloadBadCaseJsonl()}
        >
          下载 bad case JSONL
        </button>
      </div>
    </section>
  );
}

function BatchRunRow({
  index,
  question,
  selectedCitationKey,
  onSelectCitation,
  onRetry,
  submitBadCase
}: {
  index: number;
  question: BatchRunQuestionDetail;
  selectedCitationKey: string | null;
  onSelectCitation: (
    citation: Citation,
    key: string,
    response: AnswerResponse
  ) => void;
  onRetry: () => void;
  submitBadCase: (payload: BadCaseRequest) => Promise<unknown>;
}) {
  const response = question.response ?? undefined;

  return (
    <article className="batch-result-item">
      <div className="batch-result-heading">
        <strong>问题 {index + 1}</strong>
        <div className="batch-actions">
          <span className="status-chip muted">第 {question.row_index} 行</span>
          <span
            className={
              question.status === "succeeded" ? "status-chip" : "status-chip muted"
            }
          >
            {batchQuestionStatusLabel(question.status)}
          </span>
          {question.status === "failed" && (
            <button
              className="secondary-button compact-button"
              type="button"
              onClick={onRetry}
            >
              <RefreshCcw size={16} aria-hidden="true" />
              重试
            </button>
          )}
        </div>
      </div>
      <p>{question.query}</p>
      <div className="batch-original-answer">
        <span>原答案</span>
        <p>{question.input_answer.trim() || "未提供"}</p>
      </div>
      {(question.status !== "pending" || response || question.error) && (
        <div className="batch-original-answer">
          <span>模型答案</span>
          {question.status === "running" && !response && (
            <p className="meta-text">
              <LoaderCircle className="spin" size={14} aria-hidden="true" />{" "}
              正在生成回答...
            </p>
          )}
          {question.error ? (
            <p className="form-error">{question.error}</p>
          ) : (
            response && <p>{response.answer}</p>
          )}
          {response?.rewritten_query && (
            <p className="meta-text">改写问题：{response.rewritten_query}</p>
          )}
          {response && (
            <BadCasePanel
              turn={batchQuestionDetailToChatTurn(question)}
              response={response}
              submit={submitBadCase}
              initiallySubmitted={Boolean(question.bad_case)}
              selectedCitationKey={selectedCitationKey}
              onSelectCitation={(citation, key) =>
                onSelectCitation(citation, key, response)
              }
            />
          )}
        </div>
      )}
    </article>
  );
}
