import { AlertCircle, BarChart3, Clock3, Database, FileText, History } from "lucide-react";

import { ingestionStatusLabel } from "../ingestion";
import type { IngestionJobDetail } from "../types";

type IngestionDetailPanelProps = {
  detail: IngestionJobDetail | null;
  loading: boolean;
  error: string;
  onReload: () => void;
};

export function IngestionDetailPanel({ detail, loading, error, onReload }: IngestionDetailPanelProps) {
  return (
    <aside className="source-panel ingestion-detail-panel" aria-label="入库任务详情">
      <header className="ingestion-detail-header">
        <div className="pane-heading"><Database size={20} aria-hidden="true" /><h2>任务详情</h2></div>
      </header>
      <div className="ingestion-detail-scroll">
        {loading && !detail ? <p className="ingestion-detail-empty">正在加载任务详情…</p> : null}
        {error && detail && (
          <div className="ingestion-error-box" role="alert">
            <AlertCircle size={18} aria-hidden="true" />
            <div className="ingestion-detail-error-copy">
              <span>{error}</span>
              <button className="ghost-button" type="button" onClick={onReload}>
                重新加载任务详情
              </button>
            </div>
          </div>
        )}
        {!loading && !detail && !error ? (
          <p className="ingestion-detail-empty">创建或选择任务后，这里会显示统计、尝试次数与时间线。</p>
        ) : null}
        {detail && <DetailContent detail={detail} />}
      </div>
    </aside>
  );
}

function DetailContent({ detail }: { detail: IngestionJobDetail }) {
  return (
    <>
      <section className="ingestion-detail-section">
        <div className="pane-heading"><FileText size={18} aria-hidden="true" /><h3>摘要</h3></div>
        <dl className="ingestion-detail-list">
          <div><dt>目标</dt><dd>{detail.target === "case" ? "案例知识库" : "课程知识库"}</dd></div>
          <div><dt>状态</dt><dd>任务状态：{ingestionStatusLabel(detail.status)}</dd></div>
          <div><dt>尝试次数</dt><dd>{detail.attempt_count}</dd></div>
          <div><dt>当前 attempt</dt><dd>{detail.attempt?.attempt_no ?? "尚未开始"}</dd></div>
        </dl>
      </section>
      <section className="ingestion-detail-section">
        <div className="pane-heading"><BarChart3 size={18} aria-hidden="true" /><h3>统计</h3></div>
        <div className="ingestion-stat-grid">
          <span><strong>{detail.item_done}/{detail.item_total}</strong><small>输入项</small></span>
          <span><strong>{detail.document_total}</strong><small>文档</small></span>
          <span><strong>{detail.chunk_total}</strong><small>切片</small></span>
          <span><strong>{detail.warning_count}</strong><small>警告</small></span>
        </div>
      </section>
      {(detail.warning_count > 0 || detail.error_detail) && (
        <section className="ingestion-detail-section">
          <div className="pane-heading"><AlertCircle size={18} aria-hidden="true" /><h3>提示</h3></div>
          {detail.warning_count > 0 && <p>有 {detail.warning_count} 条增值处理警告。</p>}
          {detail.error_detail && <p className="error-text">{detail.error_detail}</p>}
        </section>
      )}
      <section className="ingestion-detail-section">
        <div className="pane-heading"><History size={18} aria-hidden="true" /><h3>时间线</h3></div>
        {detail.events.length === 0 ? (
          <p className="ingestion-detail-empty">暂无运行事件</p>
        ) : (
          <ol className="ingestion-timeline" aria-label="任务时间线">
            {detail.events.map((event) => (
              <li key={`${event.attempt_no}-${event.sequence}`}>
                <Clock3 size={15} aria-hidden="true" />
                <div><strong>{event.message}</strong><time dateTime={event.created_at}>{formatTime(event.created_at)}</time></div>
              </li>
            ))}
          </ol>
        )}
      </section>
    </>
  );
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(date);
}
