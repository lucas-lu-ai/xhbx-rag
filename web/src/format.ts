import type { Citation } from "./types";

export function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function numberValue(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "";
}

export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function formatEvidenceMeta(metadata?: Record<string, unknown>): string {
  if (!metadata) {
    return "";
  }
  return [stringValue(metadata.case_name), stringValue(metadata.stage)]
    .filter(Boolean)
    .join(" · ");
}

export function formatEvidenceSource(citation: Citation): string {
  const file = citation.filename || citation.source_path || "未知文件";
  const location =
    citation.display_location && citation.display_location !== "未提供精确位置"
      ? citation.display_location
      : "";
  return [file, location].filter(Boolean).join(" · ");
}

const LOCATOR_CONFIDENCE_LABELS: Record<string, string> = {
  validated_span: "精确定位",
  exact: "精确定位",
  approximate: "近似定位",
  unmatched: "原文未匹配"
};

export function formatLocatorConfidence(value?: string): string {
  return value ? LOCATOR_CONFIDENCE_LABELS[value] ?? "" : "";
}

export function formatScore(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(2)
    : "";
}

export function formatSessionTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "刚刚";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}

export function formatProcessPayload(payload?: Record<string, unknown>): string {
  if (!payload) {
    return "";
  }
  const rewrittenQuery = stringValue(payload.rewritten_query);
  if (rewrittenQuery) {
    return `改写为：${rewrittenQuery}`;
  }
  const candidateCount = numberValue(payload.candidate_count);
  if (candidateCount !== "") {
    return `候选 ${candidateCount} 条`;
  }
  const resultCount = numberValue(payload.result_count);
  if (resultCount !== "") {
    return `结果 ${resultCount} 条`;
  }
  const evidenceCount = numberValue(payload.evidence_count);
  const citationCount = numberValue(payload.citation_count);
  if (evidenceCount !== "" || citationCount !== "") {
    return [`证据 ${evidenceCount || 0} 条`, `引用 ${citationCount || 0} 条`].join(
      " · "
    );
  }
  return "";
}
