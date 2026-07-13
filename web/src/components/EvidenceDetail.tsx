import {
  ChevronDown,
  ChevronUp,
  LoaderCircle,
  Save
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

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
  index: number;
  feedback?: EvidenceFeedback;
  onSubmitFeedback?: (decision: EvidenceFeedbackDecision) => Promise<void>;
};

// 右侧引用明细：异议处理摘要、来源引用溯源与逐证据打标。
// 切换证据时由父级用 key 重新挂载，内部引用选中态随之重置。
export function EvidenceDetail({
  evidence,
  index,
  feedback,
  onSubmitFeedback
}: EvidenceDetailProps) {
  // 折叠抽取阶段留下的逐字重复引用；不同副本文件（路径不同）保留。
  const citations = dedupeCitations(evidence.citations ?? []);
  const [citationIndex, setCitationIndex] = useState(0);
  const [showAllCitations, setShowAllCitations] = useState(false);
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
  const [feedbackSaved, setFeedbackSaved] = useState(Boolean(feedback));
  const [feedbackMessage, setFeedbackMessage] = useState("");
  const [feedbackError, setFeedbackError] = useState("");
  const retrievalRadioName = useId();
  const answerUsageRadioName = useId();
  const feedbackEvidenceKey = `${index}:${
    evidence.chunk_id ?? evidence.text_preview ?? evidence.text ?? ""
  }`;
  const feedbackGenerationRef = useRef(0);
  const feedbackRequestIdRef = useRef(0);
  const activeFeedbackRequestRef = useRef<{
    generation: number;
    requestId: number;
  } | null>(null);
  const selectedCitation = citations[citationIndex];
  const citationLocation = selectedCitation
    ? [
        selectedCitation.display_location,
        formatLocatorConfidence(selectedCitation.locator_confidence)
      ]
        .filter(Boolean)
        .join(" · ") || "未提供"
    : "未提供";
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
    setFeedbackSaved(Boolean(feedback));
  }, [
    feedbackEvidenceKey,
    feedback?.answer_usage_judgement,
    feedback?.retrieval_judgement
  ]);

  useEffect(() => {
    feedbackGenerationRef.current += 1;
    activeFeedbackRequestRef.current = null;
    setSaving(false);
    setFeedbackMessage("");
    setFeedbackError("");
  }, [feedbackEvidenceKey]);

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

  async function submitDecision(
    decision: EvidenceFeedbackDecision
  ): Promise<"saved" | "failed" | "stale"> {
    if (!onSubmitFeedback) {
      return "failed";
    }
    const generation = feedbackGenerationRef.current;
    const requestId = feedbackRequestIdRef.current + 1;
    feedbackRequestIdRef.current = requestId;
    activeFeedbackRequestRef.current = { generation, requestId };
    const isCurrentRequest = () =>
      feedbackGenerationRef.current === generation &&
      activeFeedbackRequestRef.current?.generation === generation &&
      activeFeedbackRequestRef.current.requestId === requestId;
    setSaving(true);
    setFeedbackError("");
    try {
      await onSubmitFeedback(decision);
      if (!isCurrentRequest()) {
        return "stale";
      }
      setFeedbackSaved(true);
      setFeedbackMessage("已记录引用反馈。");
      return "saved";
    } catch (error) {
      if (!isCurrentRequest()) {
        return "stale";
      }
      setFeedbackError(error instanceof Error ? error.message : "无法保存反馈。");
      return "failed";
    } finally {
      if (isCurrentRequest()) {
        activeFeedbackRequestRef.current = null;
        setSaving(false);
      }
    }
  }

  async function handleAnswerUsageChange(
    next: Exclude<AnswerUsageFeedbackJudgement, "not_applicable">
  ) {
    clearFeedbackStatus();
    setAnswerUsageJudgement(next);
    setReason("");
    if (next === "correct") {
      const result = await submitDecision({
        retrieval_judgement: "accurate",
        answer_usage_judgement: "correct"
      });
      if (result === "failed") {
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
    const result = await submitDecision(decision);
    if (result === "saved") {
      setReason("");
    }
  }

  const feedbackLocked = Boolean(feedback) || saving || feedbackSaved;
  const reasonKind =
    retrievalJudgement === "inaccurate"
      ? "retrieval"
      : retrievalJudgement === "accurate" &&
          answerUsageJudgement === "incorrect"
        ? "answer"
        : null;

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
          <div className="detail-block">
            <span>位置与定位</span>
            <strong>{citationLocation}</strong>
          </div>
          <div className="excerpt-box">
            <span>原文摘录</span>
            <p>
              {selectedCitation.display_excerpt ||
                selectedCitation.quote ||
                "没有摘录内容。"}
            </p>
          </div>
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
      {onSubmitFeedback && reasonKind && !feedback && !feedbackSaved && (
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
              disabled={saving}
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
