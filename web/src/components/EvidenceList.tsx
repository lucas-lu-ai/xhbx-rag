import { Search } from "lucide-react";

import { formatEvidenceMeta, formatEvidenceSource, formatScore } from "../format";
import type { AnswerResponse, RetrievalEvidence } from "../types";

type EvidenceListProps = {
  response?: AnswerResponse;
  evidences: RetrievalEvidence[];
};

export function EvidenceList({ response, evidences }: EvidenceListProps) {
  return (
    <div className="evidence-section" aria-label="检索证据">
      <div className="pane-heading compact-heading">
        <Search size={20} aria-hidden="true" />
        <h2>检索证据</h2>
        {response && (
          <span className="evidence-count">
            {evidences.length}/{response.evidence_count}
          </span>
        )}
      </div>
      {!response ? (
        <p className="empty-source">暂无检索证据。</p>
      ) : evidences.length === 0 ? (
        <p className="empty-source">本次回答没有可展示检索证据。</p>
      ) : (
        <div
          className="evidence-scroll"
          role="region"
          aria-label="检索证据列表"
          tabIndex={0}
        >
          <div className="evidence-list">
            {evidences.map((evidence, index) => {
              const meta = formatEvidenceMeta(evidence.metadata);
              const score = formatScore(evidence.rerank_score);
              const text =
                evidence.text || evidence.text_preview || "没有正文内容。";
              const citations = evidence.citations ?? [];
              return (
                <article
                  className="evidence-item"
                  key={`${evidence.chunk_id ?? "evidence"}-${index}`}
                >
                  <div className="evidence-header">
                    <strong>
                      证据 {index + 1} · {evidence.chunk_type || "未知类型"}
                    </strong>
                    {score && <span>重排 {score}</span>}
                  </div>
                  {meta && <p className="meta-text">{meta}</p>}
                  <p className="evidence-text">{text}</p>
                  {citations.length > 0 && (
                    <div className="evidence-source-list" aria-label="证据来源">
                      {citations.map((citation, sourceIndex) => (
                        <span
                          className="evidence-source"
                          key={`${
                            citation.source_path ?? citation.filename ?? "source"
                          }-${sourceIndex}`}
                        >
                          {formatEvidenceSource(citation)}
                        </span>
                      ))}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
