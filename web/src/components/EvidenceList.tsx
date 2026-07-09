import { ChevronDown, ChevronRight, Search } from "lucide-react";
import { useState } from "react";

import {
  formatChunkType,
  formatEvidenceMeta,
  formatScore
} from "../format";
import type { RetrievalEvidence } from "../types";
import { evidenceKey } from "./EvidenceDetailContext";

type EvidenceListProps = {
  evidences: RetrievalEvidence[];
  keyPrefix: string;
  citedIndexes: Set<number>;
  selectedEvidenceKey?: string | null;
  onSelectEvidence?: (key: string) => void;
};

// 紧凑证据列表：每行只显示名称/类型/引用徽标/重排分与单行预览，
// 点击行选中后由右侧面板展示明细。
export function EvidenceList({
  evidences,
  keyPrefix,
  citedIndexes,
  selectedEvidenceKey = null,
  onSelectEvidence
}: EvidenceListProps) {
  // 默认折叠，点击标题展开/再点击折叠，避免长证据列表挤占回答区。
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="evidence-section" aria-label="检索证据">
      <button
        type="button"
        className="pane-heading compact-heading evidence-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        {expanded ? (
          <ChevronDown size={14} aria-hidden="true" />
        ) : (
          <ChevronRight size={14} aria-hidden="true" />
        )}
        <Search size={18} aria-hidden="true" />
        <strong>检索证据</strong>
        <span className="evidence-count">
          {evidences.length} 条
          {citedIndexes.size > 0 && ` · 答案引用 ${citedIndexes.size} 条`}
        </span>
      </button>
      {expanded && (
      <div className="evidence-list" role="region" aria-label="检索证据列表">
        {evidences.map((evidence, index) => {
          const cited = citedIndexes.has(index + 1);
          const key = evidenceKey(keyPrefix, index);
          const selected = key === selectedEvidenceKey;
          const meta = formatEvidenceMeta(evidence.metadata);
          const score = formatScore(evidence.rerank_score);
          const preview =
            evidence.text_preview || evidence.text || "没有正文内容。";
          const rowClass = [
            "evidence-row",
            selected ? "selected" : "",
            cited ? "cited" : ""
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <button
              className={rowClass}
              key={`${evidence.chunk_id ?? "evidence"}-${index}`}
              type="button"
              aria-pressed={selected}
              onClick={() => onSelectEvidence?.(key)}
            >
              <span className="evidence-row-index">{index + 1}</span>
              <span className="evidence-row-main">
                <span className="evidence-row-title">
                  <strong>{meta || `证据 ${index + 1}`}</strong>
                  <span className="evidence-type-chip">
                    {formatChunkType(evidence.chunk_type)}
                  </span>
                  {cited && <em className="evidence-cited-badge">答案引用</em>}
                </span>
                <span className="evidence-row-preview">{preview}</span>
              </span>
              {score && <span className="evidence-row-score">{score}</span>}
            </button>
          );
        })}
      </div>
      )}
    </div>
  );
}
