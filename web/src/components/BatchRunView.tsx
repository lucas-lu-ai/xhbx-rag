import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  Play,
  RefreshCcw
} from "lucide-react";
import { useEffect, useState } from "react";
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
  BadCaseRequest,
  BatchRunDetail,
  BatchRunQuestionDetail
} from "../types";
import { BadCasePanel } from "./BadCasePanel";
import { MarkdownMessage } from "./MarkdownMessage";
import { firstEvidenceKey, useEvidenceDetail } from "./EvidenceDetailContext";

type BatchRunViewProps = {
  runId: string;
  pollIntervalMs?: number;
  onRunMutated?: () => void;
};

export function BatchRunView({
  runId,
  pollIntervalMs,
  onRunMutated
}: BatchRunViewProps) {
  const { detail, loadError, pollError, refresh, patchDetail } =
    useBatchRunPolling(runId, { intervalMs: pollIntervalMs });
  const [actionError, setActionError] = useState("");
  const [resuming, setResuming] = useState(false);
  const [exporting, setExporting] = useState(false);
  // 列表屏/详情屏切换：记录打开的行号（row_index），null 表示列表屏。
  const [openRowIndex, setOpenRowIndex] = useState<number | null>(null);
  const { onSelectEvidence } = useEvidenceDetail();

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

  const questions = detail?.questions ?? [];
  const openPosition =
    openRowIndex === null
      ? -1
      : questions.findIndex((question) => question.row_index === openRowIndex);
  const openQuestion = openPosition >= 0 ? questions[openPosition] : undefined;

  // 进入详情或轮询补全回答后，自动选中该行第一条证据联动右侧明细。
  // 依赖用字符串 key：轮询替换 detail 对象但 key 未变时不重跑，
  // 不会覆盖用户手动选中的证据。
  const autoSelectKey = openQuestion?.response
    ? firstEvidenceKey(
        `row-${openQuestion.row_index}`,
        openQuestion.response.citations,
        openQuestion.response.retrieval_evidences ?? []
      )
    : null;
  useEffect(() => {
    if (openRowIndex === null) {
      return;
    }
    onSelectEvidence(autoSelectKey);
  }, [openRowIndex, autoSelectKey, onSelectEvidence]);

  function openRow(rowIndex: number) {
    setOpenRowIndex(rowIndex);
  }

  function backToList() {
    setOpenRowIndex(null);
    onSelectEvidence(null);
  }

  function openByPosition(position: number) {
    const target = questions[position];
    if (target) {
      setOpenRowIndex(target.row_index);
    }
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

  if (openQuestion) {
    return (
      <section className="batch-panel" aria-label="批量会话">
        <div className="batch-detail-toolbar">
          <button
            className="secondary-button compact-button"
            type="button"
            onClick={backToList}
          >
            <ArrowLeft size={16} aria-hidden="true" />
            返回列表
          </button>
          <span className="meta-text">
            第 {openPosition + 1} / {questions.length} 行
          </span>
          <span
            className={
              openQuestion.status === "succeeded"
                ? "status-chip"
                : "status-chip muted"
            }
          >
            {batchQuestionStatusLabel(openQuestion.status)}
          </span>
          {openQuestion.status === "failed" && (
            <button
              className="secondary-button compact-button"
              type="button"
              onClick={() => void handleRetry(openQuestion.row_index)}
            >
              <RefreshCcw size={16} aria-hidden="true" />
              重试
            </button>
          )}
          <div className="batch-detail-nav">
            <button
              className="secondary-button compact-button"
              type="button"
              disabled={openPosition <= 0}
              onClick={() => openByPosition(openPosition - 1)}
            >
              <ChevronLeft size={16} aria-hidden="true" />
              上一行
            </button>
            <button
              className="secondary-button compact-button"
              type="button"
              disabled={openPosition >= questions.length - 1}
              onClick={() => openByPosition(openPosition + 1)}
            >
              下一行
              <ChevronRight size={16} aria-hidden="true" />
            </button>
          </div>
        </div>
        {actionError && (
          <p className="form-error" role="alert">
            {actionError}
          </p>
        )}

        <article className="turn batch-detail-turn">
          <div className="message user-message">{openQuestion.query}</div>
          <div className="message answer-message">
            {openQuestion.status === "running" && !openQuestion.response && (
              <p className="meta-text">
                <LoaderCircle className="spin" size={14} aria-hidden="true" />{" "}
                正在生成回答...
              </p>
            )}
            {openQuestion.status === "pending" && !openQuestion.response && (
              <p className="meta-text">等待执行...</p>
            )}
            {openQuestion.error && (
              <p className="form-error">{openQuestion.error}</p>
            )}
            {openQuestion.response && (
              <MarkdownMessage content={openQuestion.response.answer} />
            )}
            {openQuestion.response?.rewritten_query && (
              <p className="meta-text">
                改写问题：{openQuestion.response.rewritten_query}
              </p>
            )}
            <div className="batch-original-answer">
              <span>人工答案</span>
              <p>{openQuestion.input_answer.trim() || "未提供"}</p>
            </div>
            {openQuestion.response && (
              <BadCasePanel
                turn={batchQuestionDetailToChatTurn(openQuestion)}
                response={openQuestion.response}
                submit={submitRowBadCase(openQuestion)}
              />
            )}
          </div>
        </article>
      </section>
    );
  }

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

      <ol className="batch-row-list" aria-label="批量问题列表">
        {detail.questions.map((question) => (
          <li className="batch-row" key={`row-${question.row_index}`}>
            <button
              className="batch-row-main"
              type="button"
              onClick={() => openRow(question.row_index)}
            >
              <span className="batch-row-index">{question.row_index}</span>
              <span className="batch-row-query">{question.query}</span>
              {question.bad_case !== null && (
                <span className="status-chip muted">已反馈</span>
              )}
              {question.status === "running" && (
                <LoaderCircle className="spin" size={14} aria-hidden="true" />
              )}
              <span
                className={
                  question.status === "succeeded"
                    ? "status-chip"
                    : "status-chip muted"
                }
              >
                {batchQuestionStatusLabel(question.status)}
              </span>
              <ChevronRight size={15} aria-hidden="true" />
            </button>
            {question.status === "failed" && (
              <button
                className="secondary-button compact-button"
                type="button"
                onClick={() => void handleRetry(question.row_index)}
              >
                <RefreshCcw size={16} aria-hidden="true" />
                重试
              </button>
            )}
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
