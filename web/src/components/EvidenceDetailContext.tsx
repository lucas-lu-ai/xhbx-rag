import { createContext, useContext } from "react";

import type { Citation, RetrievalEvidence } from "../types";

// 证据选中态与右侧明细容器的共享通道：
// App 提供容器与选中 key，EvidenceList 行点击写入选中，
// 拥有反馈状态的 BadCasePanel 把明细 portal 到容器里。
export type EvidenceDetailContextValue = {
  container: HTMLElement | null;
  selectedEvidenceKey: string | null;
  onSelectEvidence: (key: string | null) => void;
};

export const EvidenceDetailContext = createContext<EvidenceDetailContextValue>({
  container: null,
  selectedEvidenceKey: null,
  onSelectEvidence: () => {}
});

export function useEvidenceDetail(): EvidenceDetailContextValue {
  return useContext(EvidenceDetailContext);
}

// 证据的稳定选中 key（turn/row 前缀 + 证据序号），轮询替换数据后不丢选中。
export function evidenceKey(keyPrefix: string, index: number): string {
  return `${keyPrefix}:evidence-${index}`;
}

// 判断选中 key 是否属于某个 keyPrefix，是则返回证据序号（0-based）。
export function evidenceIndexForPrefix(
  key: string | null,
  keyPrefix: string
): number | null {
  const prefix = `${keyPrefix}:evidence-`;
  if (!key || !key.startsWith(prefix)) {
    return null;
  }
  const index = Number(key.slice(prefix.length));
  return Number.isInteger(index) && index >= 0 ? index : null;
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

// 回答完成后自动选中第一条证据：优先被答案引用的，其次第一条。
export function firstEvidenceKey(
  keyPrefix: string,
  citations: Citation[],
  evidences: RetrievalEvidence[]
): string | null {
  if (evidences.length === 0) {
    return null;
  }
  const cited = citedEvidenceIndexes(citations);
  const citedIndex = evidences.findIndex((_, index) => cited.has(index + 1));
  return evidenceKey(keyPrefix, citedIndex >= 0 ? citedIndex : 0);
}
