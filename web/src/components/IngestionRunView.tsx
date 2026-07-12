import {
  AlertCircle,
  CheckCircle2,
  Circle,
  Clock3,
  FileCheck2,
  LoaderCircle,
  RotateCcw,
  Trash2,
  XCircle
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ingestionStageLabel, ingestionStatusLabel, isIngestionJobActive } from "../ingestion";
import { useIngestionJobPolling } from "../hooks/useIngestionJobPolling";
import type {
  IngestionJobDetail,
  IngestionJobProgress,
  IngestionStage,
  IngestionTarget
} from "../types";

const STAGES: Array<{
  stage: Exclude<IngestionStage, "completed">;
  label: string;
  description: string;
}> = [
  { stage: "uploaded", label: "上传", description: "安全保存并完成预检" },
  { stage: "parsing", label: "解析", description: "解析文档与结构化内容" },
  { stage: "chunking", label: "切分", description: "构建并校验知识切片" },
  { stage: "indexing", label: "入库", description: "向量化并原子提交" }
];

type IngestionRunViewProps = {
  detail: IngestionJobDetail;
  actionPending: boolean;
  actionError: string;
  pollIntervalMs?: number;
  onStart: () => void;
  onRetry: () => void;
  onDelete: () => Promise<boolean>;
  onProgress: (progress: IngestionJobProgress) => void;
  onRefresh: () => void;
};

export function IngestionRunView({
  detail,
  actionPending,
  actionError,
  pollIntervalMs,
  onStart,
  onRetry,
  onDelete,
  onProgress,
  onRefresh
}: IngestionRunViewProps) {
  const shouldPoll = isIngestionJobActive(detail.status) ? detail.job_id : null;
  const { progress, error: pollingError } = useIngestionJobPolling(shouldPoll, {
    intervalMs: pollIntervalMs
  });
  const lastProgressRef = useRef("");
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const deleteButtonRef = useRef<HTMLButtonElement>(null);

  function closeDeleteConfirmation() {
    setConfirmingDelete(false);
    queueMicrotask(() => deleteButtonRef.current?.focus());
  }

  useEffect(() => {
    if (!progress) return;
    const progressKey = `${progress.status}:${progress.updated_at}`;
    if (lastProgressRef.current === progressKey) return;
    lastProgressRef.current = progressKey;
    onProgress(progress);
    onRefresh();
  }, [onProgress, onRefresh, progress]);

  const canDelete = ["draft", "succeeded", "failed"].includes(detail.status);
  const canRetry = detail.status === "failed";
  const isRollingBack = detail.status === "rolling_back";
  const statusText =
    detail.status === "succeeded" && detail.warning_count > 0
      ? "已完成 · 有警告"
      : ingestionStatusLabel(detail.status);

  return (
    <section className="ingestion-run ingestion-scroll-content" aria-labelledby="ingestion-run-title">
      <header className="ingestion-run-header">
        <div>
          <p className="eyebrow">{targetLabel(detail.target)}</p>
          <h2 id="ingestion-run-title">{detail.source_name}</h2>
        </div>
        <span className={`ingestion-status-pill status-${detail.status}`}>
          <StatusIcon status={detail.status} />
          {statusText}
        </span>
      </header>

      <ol className="ingestion-stages" aria-label="入库阶段" aria-live="polite">
        {STAGES.map((item) => {
          const state = stageState(item.stage, detail.current_stage, detail.status);
          return (
            <li
              key={item.stage}
              className={`ingestion-stage ${state}`}
              aria-current={state === "current" ? "step" : undefined}
            >
              <span className="ingestion-stage-marker"><StageIcon state={state} /></span>
              <span><strong>{item.label}</strong><small>{item.description}</small></span>
            </li>
          );
        })}
      </ol>

      {detail.status === "draft" && <DraftPreflight detail={detail} />}

      {detail.status !== "draft" && (
        <section className="ingestion-progress-card" aria-label="任务进度" aria-live="polite">
          <div className="ingestion-progress-heading">
            <span>{ingestionStageLabel(detail.current_stage)}</span>
            <strong>{detail.item_done}/{detail.item_total} 项</strong>
          </div>
          <progress
            max={Math.max(1, detail.item_total)}
            value={detail.item_done}
            aria-label="输入项处理进度"
          >
            {detail.item_done}/{detail.item_total}
          </progress>
          <p>{progress?.message || `${detail.document_total} 份文档 · ${detail.chunk_total} 个切片`}</p>
        </section>
      )}

      {isRollingBack && (
        <div className="ingestion-warning-box" role="status">
          <RotateCcw className="spin" size={19} aria-hidden="true" />
          <strong>正在恢复知识库，请勿重试或删除</strong>
        </div>
      )}

      {detail.status === "failed" && (
        <div className="ingestion-failure-box" role="alert">
          <XCircle size={21} aria-hidden="true" />
          <div>
            <strong>任务未写入知识库</strong>
            <p>{detail.error_detail || "任务处理失败，请稍后从头重试"}</p>
            <small>已完成的临时结果已丢弃；重试会从第一个输入项重新处理。</small>
          </div>
        </div>
      )}

      {detail.warning_count > 0 && (
        <div className="ingestion-warning-box" role="status">
          <AlertCircle size={19} aria-hidden="true" />
          <span>任务包含 {detail.warning_count} 条增值处理警告，核心知识已完成入库。</span>
        </div>
      )}

      {detail.status !== "draft" && <ItemList detail={detail} />}

      {(actionError || pollingError) && (
        <div className="ingestion-error-box" role="alert">
          <AlertCircle size={18} aria-hidden="true" />
          <span>{actionError || "任务进度暂时无法更新，正在自动重试"}</span>
        </div>
      )}

      <div className="ingestion-run-actions">
        {detail.status === "draft" && (
          <button className="primary-button" type="button" disabled={actionPending} onClick={onStart}>
            {actionPending && <LoaderCircle className="spin" size={17} aria-hidden="true" />}
            确认并开始
          </button>
        )}
        {(detail.status === "failed" || detail.status === "rolling_back") && (
          <button
            className="primary-button"
            type="button"
            disabled={!canRetry || actionPending}
            onClick={onRetry}
          >
            <RotateCcw size={17} aria-hidden="true" />
            从头重试
          </button>
        )}
        <button
          ref={deleteButtonRef}
          className="secondary-button danger-button"
          type="button"
          disabled={!canDelete || actionPending}
          onClick={() => setConfirmingDelete(true)}
        >
          <Trash2 size={17} aria-hidden="true" />
          删除任务
        </button>
      </div>

      {confirmingDelete && (
        <DeleteConfirmation
          pending={actionPending}
          onCancel={closeDeleteConfirmation}
          onConfirm={async () => {
            if (await onDelete()) setConfirmingDelete(false);
          }}
        />
      )}
    </section>
  );
}

function DraftPreflight({ detail }: { detail: IngestionJobDetail }) {
  const unit = detail.target === "case" ? "案例" : "课程";
  return (
    <section className="ingestion-preflight" aria-label="预检结果">
      <div className="ingestion-preflight-summary">
        <FileCheck2 size={21} aria-hidden="true" />
        <div>
          <strong>识别到 {detail.item_total} 个{unit}</strong>
          <p>共 {detail.document_total} 份文档 · 忽略 {detail.ignored_total} 项</p>
        </div>
      </div>
      <ul className="ingestion-item-list">
        {detail.items.map((item) => (
          <li key={item.item_index}>
            <span><FileCheck2 size={17} aria-hidden="true" /></span>
            <div><strong>{item.display_name}</strong><small>{item.document_count} 份文档</small></div>
          </li>
        ))}
      </ul>
      {detail.ignored_entries.length > 0 && (
        <details className="ingestion-ignored">
          <summary>查看被忽略的 {detail.ignored_total} 项</summary>
          <ul>{detail.ignored_entries.map((entry) => <li key={entry}>{entry}</li>)}</ul>
        </details>
      )}
    </section>
  );
}

function ItemList({ detail }: { detail: IngestionJobDetail }) {
  return (
    <section className="ingestion-items" aria-labelledby="ingestion-items-title">
      <div className="ingestion-section-heading">
        <h3 id="ingestion-items-title">输入项</h3>
        <span>{detail.item_total} 项 · {detail.document_total} 份文档</span>
      </div>
      <ul className="ingestion-item-list">
        {detail.items.map((item) => (
          <li key={item.item_index}>
            <span className={`item-state status-${item.status}`}><ItemStatusIcon status={item.status} /></span>
            <div>
              <strong>{item.display_name}</strong>
              <small>
                {itemStatusLabel(item.status)} · {item.document_count} 份文档 · {item.chunk_count} 个切片
              </small>
              {item.error_detail && <p className="error-text">{item.error_detail}</p>}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function DeleteConfirmation({
  pending,
  onCancel,
  onConfirm
}: {
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  return (
    <div className="ingestion-dialog-backdrop">
      <dialog
        open
        className="ingestion-dialog"
        aria-labelledby="delete-ingestion-title"
        aria-modal="true"
        onKeyDown={(event) => {
          if (event.key === "Escape" && !pending) onCancel();
        }}
        onCancel={(event) => {
          event.preventDefault();
          if (!pending) onCancel();
        }}
      >
        <h2 id="delete-ingestion-title">确认删除任务</h2>
        <p>将删除原始上传文件和任务历史，但不会删除已经成功入库的知识。</p>
        <div className="ingestion-dialog-actions">
          <button ref={cancelRef} className="ghost-button" type="button" disabled={pending} onClick={onCancel}>
            取消
          </button>
          <button className="primary-button danger-button" type="button" disabled={pending} onClick={() => void onConfirm()}>
            {pending && <LoaderCircle className="spin" size={17} aria-hidden="true" />}
            确认删除
          </button>
        </div>
      </dialog>
    </div>
  );
}

function stageState(stage: Exclude<IngestionStage, "completed">, current: IngestionStage, status: string) {
  const stageIndex = STAGES.findIndex((item) => item.stage === stage);
  const currentIndex = current === "completed" ? STAGES.length : STAGES.findIndex((item) => item.stage === current);
  if (status === "failed" && stageIndex === currentIndex) return "failed";
  if (stageIndex < currentIndex || current === "completed") return "complete";
  if (stageIndex === currentIndex) return "current";
  return "pending";
}

function StageIcon({ state }: { state: string }) {
  if (state === "complete") return <CheckCircle2 size={19} aria-hidden="true" />;
  if (state === "failed") return <XCircle size={19} aria-hidden="true" />;
  if (state === "current") return <LoaderCircle className="spin" size={19} aria-hidden="true" />;
  return <Circle size={19} aria-hidden="true" />;
}

function StatusIcon({ status }: { status: IngestionJobDetail["status"] }) {
  if (status === "succeeded") return <CheckCircle2 size={17} aria-hidden="true" />;
  if (status === "failed") return <XCircle size={17} aria-hidden="true" />;
  if (status === "rolling_back") return <RotateCcw className="spin" size={17} aria-hidden="true" />;
  if (status === "draft") return <Clock3 size={17} aria-hidden="true" />;
  return <LoaderCircle className="spin" size={17} aria-hidden="true" />;
}

function ItemStatusIcon({ status }: { status: IngestionJobDetail["items"][number]["status"] }) {
  if (status === "succeeded") return <CheckCircle2 size={17} aria-hidden="true" />;
  if (status === "failed") return <XCircle size={17} aria-hidden="true" />;
  if (status === "running") return <LoaderCircle className="spin" size={17} aria-hidden="true" />;
  return <Clock3 size={17} aria-hidden="true" />;
}

function itemStatusLabel(status: IngestionJobDetail["items"][number]["status"]) {
  return { pending: "待处理", running: "处理中", succeeded: "已完成", failed: "失败", skipped: "未执行" }[status];
}

function targetLabel(target: IngestionTarget) {
  return target === "case" ? "案例知识库" : "课程知识库";
}
