import { Search } from "lucide-react";

import {
  evidenceComplianceRisks,
  formatEvidenceMeta,
  formatEvidenceSource,
  formatLocatorConfidence,
  formatScore,
  formatTagBoost
} from "../format";
import type {
  Citation,
  EvidenceFeedback,
  EvidenceFeedbackJudgement,
  RetrievalEvidence
} from "../types";

// 证据卡片内引用的稳定 key（turn/row 前缀 + 证据序号 + 引用序号），
// 轮询替换数据后不丢选中。
export function evidenceCitationKey(
  keyPrefix: string,
  evidenceIndex: number,
  citationIndex: number
): string {
  return `${keyPrefix}:evidence-${evidenceIndex}:citation-${citationIndex}`;
}

// 从答案级引用（selected=true）收集被答案引用的证据序号（1-based）。
// 旧数据没有 selected/evidence_index 标记时返回空集合，仅少一个徽标不影响展示。
export function citedEvidenceIndexes(citations: Citation[]): Set<number> {
  const indexes = new Set<number>();
  for (const citation of citations) {
    if (citation.selected === true && typeof citation.evidence_index === "number") {
      indexes.add(citation.evidence_index);
    }
  }
  return indexes;
}

type CitationSelection = {
  citation: Citation;
  key: string;
};

// 回答完成后自动选中第一条可溯源引用：优先答案引用的证据，其次任一带引用的证据。
export function firstCitationSelection(
  keyPrefix: string,
  citations: Citation[],
  evidences: RetrievalEvidence[]
): CitationSelection | null {
  const cited = citedEvidenceIndexes(citations);
  const candidates = evidences
    .map((evidence, index) => ({ evidence, index }))
    .filter(({ evidence }) => (evidence.citations ?? []).length > 0);
  const preferred =
    candidates.find(({ index }) => cited.has(index + 1)) ?? candidates[0];
  if (preferred) {
    const citation = (preferred.evidence.citations ?? [])[0];
    return {
      citation,
      key: evidenceCitationKey(keyPrefix, preferred.index, 0)
    };
  }
  if (citations.length > 0) {
    return { citation: citations[0], key: `${keyPrefix}:citation-0` };
  }
  return null;
}

const judgementOptions: Array<{
  value: EvidenceFeedbackJudgement;
  label: string;
}> = [
  { value: "should_use", label: "应该用" },
  { value: "should_not_use", label: "不该用" },
  { value: "ranking_low", label: "排序太低" }
];

type EvidenceListProps = {
  evidences: RetrievalEvidence[];
  keyPrefix: string;
  citedIndexes: Set<number>;
  feedback: Record<string, EvidenceFeedback>;
  feedbackKeyOf: (index: number, evidence: RetrievalEvidence) => string;
  onToggleFeedback: (
    index: number,
    evidence: RetrievalEvidence,
    judgement: EvidenceFeedbackJudgement
  ) => void;
  feedbackDisabled?: boolean;
  selectedCitationKey?: string | null;
  onSelectCitation?: (citation: Citation, key: string) => void;
};

export function EvidenceList({
  evidences,
  keyPrefix,
  citedIndexes,
  feedback,
  feedbackKeyOf,
  onToggleFeedback,
  feedbackDisabled = false,
  selectedCitationKey = null,
  onSelectCitation
}: EvidenceListProps) {
  return (
    <div className="evidence-section" aria-label="检索证据">
      <div className="pane-heading compact-heading">
        <Search size={18} aria-hidden="true" />
        <strong>检索证据</strong>
        <span className="evidence-count">{evidences.length} 条</span>
      </div>
      <div className="evidence-list" role="region" aria-label="检索证据列表">
        {evidences.map((evidence, index) => {
          const cited = citedIndexes.has(index + 1);
          const meta = formatEvidenceMeta(evidence.metadata);
          const score = formatScore(evidence.rerank_score);
          const matchedTags = evidence.matched_tag_paths ?? [];
          const boostLabel = formatTagBoost(evidence.tag_boost_factor);
          const complianceRisks = evidenceComplianceRisks(evidence.metadata);
          const text = evidence.text || evidence.text_preview || "没有正文内容。";
          const citations = evidence.citations ?? [];
          const feedbackKey = feedbackKeyOf(index, evidence);
          const selectedJudgement = feedback[feedbackKey]?.judgement;
          return (
            <article
              className={cited ? "evidence-item cited" : "evidence-item"}
              key={`${evidence.chunk_id ?? "evidence"}-${index}`}
            >
              <div className="evidence-header">
                <strong>
                  证据 {index + 1} · {evidence.chunk_type || "未知类型"}
                </strong>
                <span className="evidence-header-side">
                  {cited && <em className="evidence-cited-badge">答案引用</em>}
                  {score && <span>重排 {score}</span>}
                </span>
              </div>
              {meta && <p className="meta-text">{meta}</p>}
              {(matchedTags.length > 0 || complianceRisks.length > 0) && (
                <div
                  className="evidence-tag-hits"
                  aria-label={`证据 ${index + 1} 命中标签`}
                >
                  {complianceRisks.length > 0 && (
                    <span className="evidence-compliance-badge">
                      合规注意 · {complianceRisks.join("、")}
                    </span>
                  )}
                  {boostLabel && matchedTags.length > 0 && (
                    <span className="evidence-boost-badge">
                      标签提权 {boostLabel}
                    </span>
                  )}
                  {matchedTags.map((tag) => (
                    <span className="evidence-tag-chip" key={tag}>
                      {tag}
                    </span>
                  ))}
                </div>
              )}
              <p className="evidence-text">{text}</p>
              {citations.length > 0 && (
                <div className="evidence-source-list" aria-label="证据来源">
                  {citations.map((citation, citationIndex) => {
                    const key = evidenceCitationKey(
                      keyPrefix,
                      index,
                      citationIndex
                    );
                    const confidence = formatLocatorConfidence(
                      citation.locator_confidence
                    );
                    const label = [
                      formatEvidenceSource(citation),
                      confidence === "精确定位" ? "" : confidence
                    ]
                      .filter(Boolean)
                      .join(" · ");
                    if (!onSelectCitation) {
                      return (
                        <span className="evidence-source" key={key}>
                          {label}
                        </span>
                      );
                    }
                    return (
                      <button
                        className={
                          key === selectedCitationKey
                            ? "evidence-source selectable selected"
                            : "evidence-source selectable"
                        }
                        key={key}
                        type="button"
                        aria-pressed={key === selectedCitationKey}
                        onClick={() => onSelectCitation(citation, key)}
                      >
                        {label}
                      </button>
                    );
                  })}
                </div>
              )}
              <div
                className="evidence-feedback-actions"
                aria-label={`证据 ${index + 1} 打标`}
              >
                {judgementOptions.map((option) => (
                  <label key={option.value}>
                    <input
                      type="checkbox"
                      aria-label={`证据 ${index + 1} ${option.label}`}
                      checked={selectedJudgement === option.value}
                      disabled={feedbackDisabled}
                      onChange={() =>
                        onToggleFeedback(index, evidence, option.value)
                      }
                    />
                    <span>{option.label}</span>
                  </label>
                ))}
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
