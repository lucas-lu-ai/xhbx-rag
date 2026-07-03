import { ChevronDown, ChevronUp } from "lucide-react";
import { useEffect, useState } from "react";

import type { Citation } from "../types";

const MAX_COLLAPSED_CITATIONS = 3;

// 引用选中态使用稳定 key（前缀 + 引用序号），轮询替换数据后不丢选中。
export function citationKey(keyPrefix: string, index: number): string {
  return `${keyPrefix}:${index}`;
}

type CitationListProps = {
  citations: Citation[];
  keyPrefix: string;
  selectedKey: string | null;
  onSelect: (citation: Citation, key: string) => void;
};

export function CitationList({
  citations,
  keyPrefix,
  selectedKey,
  onSelect
}: CitationListProps) {
  const [expanded, setExpanded] = useState(false);
  const canToggle = citations.length > MAX_COLLAPSED_CITATIONS;
  const visibleCitations =
    expanded || !canToggle
      ? citations
      : citations.slice(0, MAX_COLLAPSED_CITATIONS);

  useEffect(() => {
    setExpanded(false);
  }, [keyPrefix, citations.length]);

  return (
    <div className="citation-list" aria-label="引用列表">
      {citations.length === 0 ? (
        <span className="meta-text">没有可展示引用。</span>
      ) : (
        <>
          {visibleCitations.map((citation, index) => {
            const key = citationKey(keyPrefix, index);
            return (
              <button
                className={
                  key === selectedKey ? "citation-chip selected" : "citation-chip"
                }
                key={key}
                type="button"
                aria-pressed={key === selectedKey}
                onClick={() => onSelect(citation, key)}
              >
                引用 {index + 1} · {citation.filename || "未知文件"} ·{" "}
                {citation.display_location || "未提供精确位置"}
              </button>
            );
          })}
          {canToggle && (
            <button
              className="inline-button citation-toggle"
              type="button"
              aria-expanded={expanded}
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? (
                <ChevronUp size={16} aria-hidden="true" />
              ) : (
                <ChevronDown size={16} aria-hidden="true" />
              )}
              {expanded ? "收起" : "显示更多"}
            </button>
          )}
        </>
      )}
    </div>
  );
}
