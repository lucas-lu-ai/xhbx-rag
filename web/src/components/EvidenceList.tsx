import { ChevronDown, ChevronRight, Search } from "lucide-react";
import { useState } from "react";

import {
  formatChunkType,
  formatEvidenceMeta,
  formatScore
} from "../format";
import type { RetrievalEvidence } from "../types";
import { citedEvidenceEntries, evidenceKey } from "./EvidenceDetailContext";

type EvidenceListProps = {
  evidences: RetrievalEvidence[];
  keyPrefix: string;
  citedIndexes: Set<number>;
  selectedEvidenceKey?: string | null;
  onSelectEvidence?: (key: string) => void;
};

// 紧凑知识引用列表：每行只显示名称/类型/重排分与单行预览，
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
  const citedEntries = citedEvidenceEntries(evidences, citedIndexes);
  if (citedEntries.length === 0) return null;

  return (
    <div className="evidence-section" aria-label="知识引用">
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
        <strong>知识引用</strong>
        <span className="evidence-count">
          {citedEntries.length} 条
        </span>
      </button>
      {expanded && (
        <div className="evidence-list" role="region" aria-label="知识引用列表">
          {citedEntries.map(({ evidence, evidenceIndex, displayIndex }) => {
            const key = evidenceKey(keyPrefix, evidenceIndex);
            const selected = key === selectedEvidenceKey;
            const meta = formatEvidenceMeta(evidence.metadata) || "未命名知识";
            const score = formatScore(evidence.rerank_score);
            const preview =
              evidence.text_preview || evidence.text || "没有正文内容。";
            const rowClass = ["evidence-row", selected ? "selected" : ""]
              .filter(Boolean)
              .join(" ");
            return (
              <button
                className={rowClass}
                key={`${evidence.chunk_id ?? "evidence"}-${evidenceIndex}`}
                type="button"
                aria-pressed={selected}
                onClick={() => onSelectEvidence?.(key)}
              >
                <span className="evidence-row-index">{displayIndex + 1}</span>
                <span className="evidence-row-main">
                  <span className="evidence-row-title">
                    <strong>{meta}</strong>
                    <span className="evidence-type-chip">
                      {formatChunkType(evidence.chunk_type)}
                    </span>
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
