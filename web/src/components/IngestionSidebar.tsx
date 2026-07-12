import {
  AlertCircle,
  CheckCircle2,
  Clock3,
  LoaderCircle,
  Plus,
  RefreshCw,
  RotateCcw,
  Trash2
} from "lucide-react";

import { ingestionStatusLabel } from "../ingestion";
import type { IngestionJobStatus, IngestionJobSummary } from "../types";
import { WorkspaceNav } from "./WorkspaceNav";

type IngestionSidebarProps = {
  jobs: IngestionJobSummary[];
  selectedJobId?: string;
  loading: boolean;
  error: string;
  onSelect: (jobId: string) => void;
  onCreate: () => void;
  onRefresh: () => void;
  onOpenChat: () => void;
};

export function IngestionSidebar({
  jobs,
  selectedJobId,
  loading,
  error,
  onSelect,
  onCreate,
  onRefresh,
  onOpenChat
}: IngestionSidebarProps) {
  return (
    <aside className="session-panel ingestion-sidebar" aria-label="入库任务列表">
      <WorkspaceNav
        currentView="ingestion"
        onNavigate={(view) => {
          if (view === "chat") onOpenChat();
        }}
      />
      <header className="ingestion-sidebar-header">
        <div>
          <p className="eyebrow">任务</p>
          <h2>入库历史</h2>
        </div>
        <button className="icon-button" type="button" aria-label="刷新任务列表" onClick={onRefresh}>
          <RefreshCw size={17} aria-hidden="true" />
        </button>
      </header>
      <button className="primary-button ingestion-new-button" type="button" onClick={onCreate}>
        <Plus size={18} aria-hidden="true" />
        新建任务
      </button>
      {error && (
        <div className="ingestion-sidebar-error" role="alert">
          <AlertCircle size={17} aria-hidden="true" />
          <span>{error}</span>
        </div>
      )}
      <details className="ingestion-history" open>
        <summary>任务列表</summary>
        <nav className="ingestion-job-list" aria-label="历史入库任务" aria-busy={loading}>
          {loading && jobs.length === 0 ? (
            <p className="ingestion-list-state">
              <LoaderCircle className="spin" size={17} aria-hidden="true" />
              正在加载任务…
            </p>
          ) : jobs.length === 0 ? (
            <p className="ingestion-list-state">暂无入库任务</p>
          ) : (
            jobs.map((job) => (
              <button
                key={job.job_id}
                type="button"
                className={
                  selectedJobId === job.job_id ? "ingestion-job-row selected" : "ingestion-job-row"
                }
                aria-pressed={selectedJobId === job.job_id}
                onClick={() => onSelect(job.job_id)}
              >
                <span className={`ingestion-status-icon status-${job.status}`}>
                  <StatusIcon status={job.status} />
                </span>
                <span className="ingestion-job-copy">
                  <strong>{job.source_name}</strong>
                  <small>
                    {job.target === "case" ? "案例知识库" : "课程知识库"} ·{" "}
                    {ingestionStatusLabel(job.status)}
                    {job.status === "succeeded" && job.warning_count > 0 ? " · 有警告" : ""}
                  </small>
                </span>
              </button>
            ))
          )}
        </nav>
      </details>
    </aside>
  );
}

function StatusIcon({ status }: { status: IngestionJobStatus }) {
  const props = { size: 17, "aria-hidden": true as const };
  if (status === "succeeded") return <CheckCircle2 {...props} />;
  if (status === "failed") return <AlertCircle {...props} />;
  if (status === "rolling_back") return <RotateCcw {...props} />;
  if (status === "deleting") return <Trash2 {...props} />;
  if (status === "queued" || status === "running") return <LoaderCircle className="spin" {...props} />;
  return <Clock3 {...props} />;
}
