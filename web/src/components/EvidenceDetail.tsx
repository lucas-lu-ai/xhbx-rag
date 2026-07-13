import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  ExternalLink,
  LoaderCircle,
  Save
} from "lucide-react";
import { Fragment, useEffect, useId, useState } from "react";

import { revealSource } from "../api";
import {
  parseEvidenceText,
  type EvidenceTextSegment
} from "../evidenceText";
import {
  dedupeCitations,
  formatEvidenceMeta,
  formatEvidenceSource,
  formatEvidenceSourceCompact,
  formatLocatorConfidence,
  formatScore
} from "../format";
import type {
  AnswerUsageFeedbackJudgement,
  EvidenceFeedback,
  EvidenceFeedbackDecision,
  RetrievalFeedbackJudgement,
  RetrievalEvidence
} from "../types";

// 案例原文块默认折叠：原文摘录冗长且是次要参考，点击标题展开/折叠。
function EvidenceSourceBlock({
  label,
  items
}: {
  label: string;
  items: string[];
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="evidence-struct-block">
      <button
        type="button"
        className="evidence-source-block-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        {expanded ? (
          <ChevronDown size={13} aria-hidden="true" />
        ) : (
          <ChevronRight size={13} aria-hidden="true" />
        )}
        <span className="evidence-field-label source">{label}</span>
      </button>
      {expanded && (
        <ul>
          {items.map((item, itemIndex) => (
            <li key={`item-${itemIndex}`}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

type RelatedScriptDetail = {
  script_id: string;
  stage?: string;
  scenario?: string;
  customer_trigger?: string;
  goal?: string;
  source_quote?: string;
  coach_wording?: string;
  strategy_names?: string[];
  follow_up_questions?: string[];
  compliance_notes?: string[];
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function splitDelimited(value: string): string[] {
  return value
    .split(/[、,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function stringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }
  if (typeof value === "string") {
    return splitDelimited(value);
  }
  return [];
}

function relatedScriptFromRecord(
  record: Record<string, unknown>
): RelatedScriptDetail | null {
  const scriptId = stringValue(record.script_id);
  if (!scriptId) {
    return null;
  }
  return {
    script_id: scriptId,
    stage: stringValue(record.stage) || undefined,
    scenario: stringValue(record.scenario) || undefined,
    customer_trigger: stringValue(record.customer_trigger) || undefined,
    goal: stringValue(record.goal) || undefined,
    source_quote: stringValue(record.source_quote) || undefined,
    coach_wording: stringValue(record.coach_wording) || undefined,
    strategy_names: stringList(record.strategy_names),
    follow_up_questions: stringList(record.follow_up_questions),
    compliance_notes: stringList(record.compliance_notes)
  };
}

function relatedScriptFromEvidence(
  evidence: RetrievalEvidence
): RelatedScriptDetail | null {
  const metadata = evidence.metadata ?? {};
  const segments = parseEvidenceText(evidence.text || "");
  const fields = new Map<string, string>();
  const blocks = new Map<string, string[]>();
  for (const segment of segments) {
    if (segment.kind === "field") {
      fields.set(segment.label, segment.value);
    }
    if (segment.kind === "block") {
      blocks.set(segment.label, segment.items);
    }
  }
  const scriptId = stringValue(metadata.script_id) || fields.get("话术 ID") || "";
  if (!scriptId) {
    return null;
  }
  return {
    script_id: scriptId,
    stage: stringValue(metadata.stage) || fields.get("阶段") || undefined,
    scenario: stringValue(metadata.scenario) || fields.get("场景") || undefined,
    customer_trigger:
      stringValue(metadata.customer_trigger) ||
      fields.get("客户触发点") ||
      undefined,
    goal: fields.get("目标") || undefined,
    source_quote: fields.get("原始话术") || undefined,
    coach_wording: fields.get("教练推荐话术") || undefined,
    strategy_names:
      stringList(metadata.strategy_names).length > 0
        ? stringList(metadata.strategy_names)
        : splitDelimited(fields.get("关联策略") || ""),
    follow_up_questions: blocks.get("追问建议") ?? [],
    compliance_notes: blocks.get("合规提醒") ?? []
  };
}

function buildRelatedScriptLookup(
  evidence: RetrievalEvidence,
  relatedEvidences: RetrievalEvidence[]
): Map<string, RelatedScriptDetail> {
  const lookup = new Map<string, RelatedScriptDetail>();
  const metadata = evidence.metadata ?? {};
  const currentCaseName = stringValue(metadata.case_name);
  const metadataDetails = Array.isArray(metadata.related_script_details)
    ? metadata.related_script_details
    : [];

  for (const item of metadataDetails) {
    const detail = relatedScriptFromRecord(asRecord(item) ?? {});
    if (detail) {
      lookup.set(detail.script_id, detail);
    }
  }

  for (const candidate of relatedEvidences) {
    if (candidate.chunk_type !== "script") {
      continue;
    }
    const candidateCaseName = stringValue(candidate.metadata?.case_name);
    if (
      currentCaseName &&
      candidateCaseName &&
      candidateCaseName !== currentCaseName
    ) {
      continue;
    }
    const detail = relatedScriptFromEvidence(candidate);
    if (detail && !lookup.has(detail.script_id)) {
      lookup.set(detail.script_id, detail);
    }
  }

  return lookup;
}

function splitRelatedScriptIds(value: string): string[] {
  return value
    .split(/[、,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function safeDomId(value: string): string {
  return value.replace(/[^A-Za-z0-9_-]/g, "-");
}

function DetailLine({ label, value }: { label: string; value?: string }) {
  if (!value) {
    return null;
  }
  return (
    <p className="related-script-detail-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </p>
  );
}

function DetailList({ label, items }: { label: string; items?: string[] }) {
  if (!items || items.length === 0) {
    return null;
  }
  return (
    <div className="related-script-detail-list">
      <span>{label}</span>
      <ul>
        {items.map((item, index) => (
          <li key={`${label}-${index}`}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function RelatedScriptDetailCard({
  detail,
  id
}: {
  detail: RelatedScriptDetail;
  id: string;
}) {
  return (
    <div className="related-script-detail" id={id}>
      <div className="related-script-detail-heading">
        <strong>{detail.script_id}</strong>
        {detail.stage && <span>{detail.stage}</span>}
      </div>
      <DetailLine label="场景" value={detail.scenario} />
      <DetailLine label="客户触发点" value={detail.customer_trigger} />
      <DetailLine label="目标" value={detail.goal} />
      <DetailLine label="原始话术" value={detail.source_quote} />
      <DetailLine label="教练推荐话术" value={detail.coach_wording} />
      <DetailList label="关联策略" items={detail.strategy_names} />
      <DetailList label="追问建议" items={detail.follow_up_questions} />
      <DetailList label="合规提醒" items={detail.compliance_notes} />
    </div>
  );
}

function RelatedScriptRow({
  label,
  value,
  scriptLookup
}: {
  label: string;
  value: string;
  scriptLookup: Map<string, RelatedScriptDetail>;
}) {
  const baseId = useId();
  const scriptIds = splitRelatedScriptIds(value);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  if (scriptIds.length === 0 || !scriptIds.some((id) => scriptLookup.has(id))) {
    return (
      <p className="evidence-struct-row">
        <span className="evidence-field-label">{label}</span>
        <span className="evidence-field-value">{value}</span>
      </p>
    );
  }

  function toggleScript(scriptId: string) {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(scriptId)) {
        next.delete(scriptId);
      } else {
        next.add(scriptId);
      }
      return next;
    });
  }

  return (
    <>
      <span className="evidence-field-label">{label}</span>
      <span className="evidence-field-value related-script-tokens">
        {scriptIds.map((scriptId, index) => {
          const detail = scriptLookup.get(scriptId);
          const detailId = `${baseId}-${safeDomId(scriptId)}`;
          return (
            <Fragment key={scriptId}>
              {index > 0 && (
                <span className="related-script-separator">、</span>
              )}
              {detail ? (
                <button
                  type="button"
                  className="related-script-token"
                  aria-expanded={expandedIds.has(scriptId)}
                  aria-controls={detailId}
                  onClick={() => toggleScript(scriptId)}
                >
                  {scriptId}
                </button>
              ) : (
                <span>{scriptId}</span>
              )}
            </Fragment>
          );
        })}
      </span>
      {scriptIds.map((scriptId) => {
        const detail = scriptLookup.get(scriptId);
        if (!detail || !expandedIds.has(scriptId)) {
          return null;
        }
        return (
          <RelatedScriptDetailCard
            detail={detail}
            id={`${baseId}-${safeDomId(scriptId)}`}
            key={`detail-${scriptId}`}
          />
        );
      })}
    </>
  );
}

// 结构化正文：模型归纳字段与案例原文字段用不同颜色标签区分。
function EvidenceStructuredText({
  segments,
  scriptLookup
}: {
  segments: EvidenceTextSegment[];
  scriptLookup: Map<string, RelatedScriptDetail>;
}) {
  return (
    <div className="evidence-text evidence-struct">
      <p className="evidence-struct-legend">
        <span className="evidence-field-label">AI 归纳</span>
        <span className="evidence-field-label source">案例原文</span>
      </p>
      {segments.map((segment, index) => {
        if (segment.kind === "plain") {
          return (
            <p className="evidence-struct-plain" key={`segment-${index}`}>
              {segment.value}
            </p>
          );
        }
        if (segment.kind === "field") {
          if (segment.label === "关联话术") {
            return (
              <RelatedScriptRow
                key={`segment-${index}`}
                label={segment.label}
                value={segment.value}
                scriptLookup={scriptLookup}
              />
            );
          }
          return (
            <p className="evidence-struct-row" key={`segment-${index}`}>
              <span
                className={
                  segment.origin === "source"
                    ? "evidence-field-label source"
                    : "evidence-field-label"
                }
              >
                {segment.label}
              </span>
              <span className="evidence-field-value">{segment.value}</span>
            </p>
          );
        }
        if (segment.origin === "source") {
          return (
            <EvidenceSourceBlock
              key={`segment-${index}`}
              label={segment.label}
              items={segment.items}
            />
          );
        }
        return (
          <div className="evidence-struct-block" key={`segment-${index}`}>
            <span className="evidence-field-label">{segment.label}</span>
            <ul>
              {segment.items.map((item, itemIndex) => (
                <li key={`item-${itemIndex}`}>{item}</li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

const OBJECTION_DETAIL_LABELS = [
  "客户异议",
  "异议诊断",
  "推荐回应"
] as const;

function EvidenceObjectionText({
  segments
}: {
  segments: EvidenceTextSegment[];
}) {
  const values = new Map(
    segments.flatMap((segment) =>
      segment.kind === "field" &&
      OBJECTION_DETAIL_LABELS.some((label) => label === segment.label)
        ? [[segment.label, segment.value] as const]
        : []
    )
  );
  const visibleLabels = OBJECTION_DETAIL_LABELS.filter((label) =>
    values.has(label)
  );

  if (visibleLabels.length === 0) {
    return <p className="evidence-text">暂无异议处理内容。</p>;
  }

  return (
    <div className="evidence-text evidence-struct">
      {visibleLabels.map((label) => (
        <p className="evidence-struct-row" key={label}>
          <span className="evidence-field-label">{label}</span>
          <span className="evidence-field-value">{values.get(label)}</span>
        </p>
      ))}
    </div>
  );
}

// 来源引用默认最多显示这么多条，超出的收进“展开其余”，避免多副本/多片段刷屏。
const MAX_VISIBLE_CITATIONS = 4;

type EvidenceDetailProps = {
  evidence: RetrievalEvidence;
  relatedEvidences?: RetrievalEvidence[];
  index: number;
  feedback?: EvidenceFeedback;
  onSubmitFeedback?: (decision: EvidenceFeedbackDecision) => Promise<void>;
};

// 右侧引用明细：异议处理摘要、来源引用溯源与逐证据打标。
// 切换证据时由父级用 key 重新挂载，内部引用选中态随之重置。
export function EvidenceDetail({
  evidence,
  relatedEvidences = [],
  index,
  feedback,
  onSubmitFeedback
}: EvidenceDetailProps) {
  // 折叠抽取阶段留下的逐字重复引用；不同副本文件（路径不同）保留。
  const citations = dedupeCitations(evidence.citations ?? []);
  const [citationIndex, setCitationIndex] = useState(0);
  const [showAllCitations, setShowAllCitations] = useState(false);
  const [revealMessage, setRevealMessage] = useState("");
  const [retrievalJudgement, setRetrievalJudgement] = useState<
    RetrievalFeedbackJudgement | undefined
  >(feedback?.retrieval_judgement);
  const [answerUsageJudgement, setAnswerUsageJudgement] = useState<
    Exclude<AnswerUsageFeedbackJudgement, "not_applicable"> | undefined
  >(
    feedback?.answer_usage_judgement === "correct" ||
      feedback?.answer_usage_judgement === "incorrect"
      ? feedback.answer_usage_judgement
      : undefined
  );
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [feedbackMessage, setFeedbackMessage] = useState("");
  const [feedbackError, setFeedbackError] = useState("");
  const retrievalRadioName = useId();
  const answerUsageRadioName = useId();
  const selectedCitation = citations[citationIndex];
  const meta = formatEvidenceMeta(evidence.metadata);
  const citationNumber = index + 1;
  const knowledgeName = meta || "未命名知识";
  const score = formatScore(evidence.rerank_score);
  const text = evidence.text || evidence.text_preview || "没有正文内容。";
  const textSegments = parseEvidenceText(text);

  useEffect(() => {
    setRetrievalJudgement(feedback?.retrieval_judgement);
    setAnswerUsageJudgement(
      feedback?.answer_usage_judgement === "correct" ||
        feedback?.answer_usage_judgement === "incorrect"
        ? feedback.answer_usage_judgement
        : undefined
    );
    setReason("");
  }, [
    evidence.chunk_id,
    feedback?.answer_usage_judgement,
    feedback?.retrieval_judgement,
    index
  ]);

  function clearFeedbackStatus() {
    setFeedbackMessage("");
    setFeedbackError("");
  }

  function handleRetrievalChange(next: RetrievalFeedbackJudgement) {
    clearFeedbackStatus();
    setRetrievalJudgement(next);
    setAnswerUsageJudgement(undefined);
    setReason("");
  }

  async function submitDecision(decision: EvidenceFeedbackDecision) {
    if (!onSubmitFeedback) {
      return false;
    }
    setSaving(true);
    setFeedbackError("");
    try {
      await onSubmitFeedback(decision);
      setFeedbackMessage("已记录引用反馈。");
      return true;
    } catch (error) {
      setFeedbackError(error instanceof Error ? error.message : "无法保存反馈。");
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function handleAnswerUsageChange(
    next: Exclude<AnswerUsageFeedbackJudgement, "not_applicable">
  ) {
    clearFeedbackStatus();
    setAnswerUsageJudgement(next);
    setReason("");
    if (next === "correct") {
      const saved = await submitDecision({
        retrieval_judgement: "accurate",
        answer_usage_judgement: "correct"
      });
      if (!saved) {
        setAnswerUsageJudgement(undefined);
      }
    }
  }

  function handleReasonCancel() {
    clearFeedbackStatus();
    if (retrievalJudgement === "inaccurate") {
      setRetrievalJudgement(undefined);
      setAnswerUsageJudgement(undefined);
    } else {
      setAnswerUsageJudgement(undefined);
    }
    setReason("");
  }

  async function handleReasonSubmit() {
    const trimmed = reason.trim();
    if (!trimmed) {
      return;
    }
    const decision: EvidenceFeedbackDecision | null =
      retrievalJudgement === "inaccurate"
        ? {
            retrieval_judgement: "inaccurate",
            answer_usage_judgement: "not_applicable",
            reason: trimmed
          }
        : retrievalJudgement === "accurate" &&
            answerUsageJudgement === "incorrect"
          ? {
              retrieval_judgement: "accurate",
              answer_usage_judgement: "incorrect",
              reason: trimmed
            }
          : null;
    if (!decision) {
      return;
    }
    const saved = await submitDecision(decision);
    if (saved) {
      setReason("");
    }
  }

  const feedbackLocked = Boolean(feedback) || saving;
  const reasonKind =
    retrievalJudgement === "inaccurate"
      ? "retrieval"
      : retrievalJudgement === "accurate" &&
          answerUsageJudgement === "incorrect"
        ? "answer"
        : null;

  async function handleReveal() {
    if (!selectedCitation?.source_path) {
      return;
    }
    try {
      await revealSource({ source_path: selectedCitation.source_path });
      setRevealMessage("已在 Finder 中显示文件。");
    } catch (error) {
      setRevealMessage(error instanceof Error ? error.message : "无法显示文件。");
    }
  }

  return (
    <article
      className="evidence-detail"
      aria-label={`引用${citationNumber}明细`}
    >
      <div className="evidence-header">
        <strong>引用{citationNumber}：{knowledgeName}</strong>
        <span className="evidence-header-side">
          {score && <span>重排 {score}</span>}
        </span>
      </div>
      <EvidenceObjectionText segments={textSegments} />
      {citations.length > 0 && (
        <div className="evidence-source-list" aria-label="证据来源">
          {(showAllCitations
            ? citations
            : citations.slice(0, MAX_VISIBLE_CITATIONS)
          ).map((citation, itemIndex) => {
            const confidence = formatLocatorConfidence(
              citation.locator_confidence
            );
            // 按钮用紧凑来源（去掉课程章节长路径），完整位置留在下方“位置”块。
            const label = [
              formatEvidenceSourceCompact(citation),
              confidence === "精确定位" ? "" : confidence
            ]
              .filter(Boolean)
              .join(" · ");
            return (
              <button
                className={
                  itemIndex === citationIndex
                    ? "evidence-source selectable selected"
                    : "evidence-source selectable"
                }
                key={`citation-${itemIndex}`}
                type="button"
                title={formatEvidenceSource(citation)}
                aria-pressed={itemIndex === citationIndex}
                onClick={() => {
                  setCitationIndex(itemIndex);
                  setRevealMessage("");
                }}
              >
                {label}
              </button>
            );
          })}
          {citations.length > MAX_VISIBLE_CITATIONS && (
            <button
              className="evidence-source-more"
              type="button"
              aria-expanded={showAllCitations}
              onClick={() => setShowAllCitations((value) => !value)}
            >
              {showAllCitations ? (
                <>
                  <ChevronUp size={13} aria-hidden="true" />
                  收起来源引用
                </>
              ) : (
                <>
                  <ChevronDown size={13} aria-hidden="true" />
                  展开其余 {citations.length - MAX_VISIBLE_CITATIONS} 条
                </>
              )}
            </button>
          )}
        </div>
      )}
      {selectedCitation && (
        <div className="source-stack">
          <div className="detail-block">
            <span>文件</span>
            <strong>
              {selectedCitation.source_path ||
                selectedCitation.filename ||
                "未知文件"}
            </strong>
          </div>
          <div className="detail-grid">
            <div className="detail-block">
              <span>位置</span>
              <strong>
                {selectedCitation.display_location || "未提供精确位置"}
              </strong>
            </div>
            <div className="detail-block">
              <span>定位</span>
              <strong>
                {formatLocatorConfidence(selectedCitation.locator_confidence) ||
                  "未提供"}
              </strong>
            </div>
          </div>
          <div className="excerpt-box">
            <span>原文摘录</span>
            <p>
              {selectedCitation.display_excerpt ||
                selectedCitation.quote ||
                "没有摘录内容。"}
            </p>
          </div>
          <button
            className="secondary-button"
            type="button"
            disabled={!selectedCitation.can_reveal}
            onClick={() => void handleReveal()}
          >
            <ExternalLink size={18} aria-hidden="true" />
            在 Finder 中显示文件
          </button>
          {revealMessage && <p className="meta-text">{revealMessage}</p>}
        </div>
      )}
      {onSubmitFeedback && (
        <div className="evidence-feedback">
          <fieldset className="evidence-feedback-dimension">
            <legend>召回是否准确？</legend>
            <div className="evidence-feedback-actions">
              <label>
                <input
                  type="radio"
                  name={retrievalRadioName}
                  aria-label={`引用${citationNumber}召回准确`}
                  checked={retrievalJudgement === "accurate"}
                  disabled={feedbackLocked}
                  onChange={() => handleRetrievalChange("accurate")}
                />
                <span>准确</span>
              </label>
              <label>
                <input
                  type="radio"
                  name={retrievalRadioName}
                  aria-label={`引用${citationNumber}召回不准确`}
                  checked={retrievalJudgement === "inaccurate"}
                  disabled={feedbackLocked}
                  onChange={() => handleRetrievalChange("inaccurate")}
                />
                <span>不准确</span>
              </label>
            </div>
          </fieldset>
          {retrievalJudgement === "accurate" && (
            <fieldset className="evidence-feedback-dimension">
              <legend>回答是否正确参考该引用？</legend>
              <div className="evidence-feedback-actions">
                <label>
                  <input
                    type="radio"
                    name={answerUsageRadioName}
                    aria-label={`引用${citationNumber}参考正确`}
                    checked={answerUsageJudgement === "correct"}
                    disabled={feedbackLocked}
                    onChange={() => void handleAnswerUsageChange("correct")}
                  />
                  <span>参考正确</span>
                </label>
                <label>
                  <input
                    type="radio"
                    name={answerUsageRadioName}
                    aria-label={`引用${citationNumber}参考不正确`}
                    checked={answerUsageJudgement === "incorrect"}
                    disabled={feedbackLocked}
                    onChange={() => void handleAnswerUsageChange("incorrect")}
                  />
                  <span>参考不正确</span>
                </label>
              </div>
            </fieldset>
          )}
        </div>
      )}
      {onSubmitFeedback && reasonKind && !feedback && (
        <div className="evidence-feedback-reason-form">
          <label className="text-field">
            <span>
              {reasonKind === "retrieval"
                ? "召回不准确原因"
                : "参考不正确原因"}
            </span>
            <textarea
              rows={3}
              value={reason}
              onChange={(event) => {
                setReason(event.target.value);
                setFeedbackError("");
              }}
              placeholder={
                reasonKind === "retrieval"
                  ? "例如：该引用与客户问题无关、客户或案例不匹配，未能回答当前异议。"
                  : "例如：回答曲解了引用原意、超出证据范围，或遗漏了关键限制。"
              }
            />
          </label>
          <div className="evidence-feedback-reason-actions">
            <button
              className="secondary-button compact-button"
              type="button"
              disabled={saving || !reason.trim()}
              onClick={() => void handleReasonSubmit()}
            >
              {saving ? (
                <LoaderCircle className="spin" size={16} aria-hidden="true" />
              ) : (
                <Save size={16} aria-hidden="true" />
              )}
              保存反馈
            </button>
            <button
              className="inline-button compact-button"
              type="button"
              disabled={saving}
              onClick={handleReasonCancel}
            >
              取消
            </button>
          </div>
        </div>
      )}
      {feedbackError && <p className="form-error">{feedbackError}</p>}
      {feedbackMessage && <p className="success-text">{feedbackMessage}</p>}
    </article>
  );
}
