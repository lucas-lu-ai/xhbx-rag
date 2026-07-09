import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  ExternalLink,
  LoaderCircle,
  Save
} from "lucide-react";
import { useState } from "react";

import { revealSource } from "../api";
import {
  hasStructuredFields,
  parseEvidenceText,
  type EvidenceTextSegment
} from "../evidenceText";
import {
  dedupeCitations,
  evidenceComplianceRisks,
  formatChunkType,
  formatEvidenceMeta,
  formatEvidenceSource,
  formatEvidenceSourceCompact,
  formatLocatorConfidence,
  formatScore,
  formatTagBoost
} from "../format";
import type {
  EvidenceFeedbackJudgement,
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

// 结构化正文：模型归纳字段与案例原文字段用不同颜色标签区分。
function EvidenceStructuredText({
  segments
}: {
  segments: EvidenceTextSegment[];
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

// 来源引用默认最多显示这么多条，超出的收进“展开其余”，避免多副本/多片段刷屏。
const MAX_VISIBLE_CITATIONS = 4;

type EvidenceDetailProps = {
  evidence: RetrievalEvidence;
  index: number;
  cited: boolean;
  feedbackJudgement?: EvidenceFeedbackJudgement;
  onToggleFeedback?: (judgement: EvidenceFeedbackJudgement) => void;
  // “应该用”提交入口：点击后立即落地一条正向 bad case（无需理由）。
  onSubmitUseful?: () => Promise<void>;
  // “不该用”提交入口：填写理由后立即落地 bad case（聊天/批量各自注入）。
  onSubmitNotUseful?: (reason: string) => Promise<void>;
};

// 右侧证据明细：正文全文、标签命中、来源引用溯源与逐证据打标。
// 切换证据时由父级用 key 重新挂载，内部引用选中态随之重置。
export function EvidenceDetail({
  evidence,
  index,
  cited,
  feedbackJudgement,
  onToggleFeedback,
  onSubmitUseful,
  onSubmitNotUseful
}: EvidenceDetailProps) {
  // 折叠抽取阶段留下的逐字重复引用；不同副本文件（路径不同）保留。
  const citations = dedupeCitations(evidence.citations ?? []);
  const [citationIndex, setCitationIndex] = useState(0);
  const [showAllCitations, setShowAllCitations] = useState(false);
  const [revealMessage, setRevealMessage] = useState("");
  const [reasonOpen, setReasonOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [feedbackMessage, setFeedbackMessage] = useState("");
  const [feedbackError, setFeedbackError] = useState("");
  // 打开理由框前的判定，取消时恢复（应该用/不该用是单选互斥）。
  const [previousJudgement, setPreviousJudgement] =
    useState<EvidenceFeedbackJudgement | null>(null);
  const selectedCitation = citations[citationIndex];
  const meta = formatEvidenceMeta(evidence.metadata);
  const score = formatScore(evidence.rerank_score);
  const matchedTags = evidence.matched_tag_paths ?? [];
  const boostLabel = formatTagBoost(evidence.tag_boost_factor);
  const complianceRisks = evidenceComplianceRisks(evidence.metadata);
  const text = evidence.text || evidence.text_preview || "没有正文内容。";
  const textSegments = parseEvidenceText(text);

  // 点击“应该用”立即选中并落地一条正向 bad case（无需理由）；
  // 已勾选时再点则取消本地判定，已落地的 bad case 不撤回。
  async function handleUsefulToggle() {
    setFeedbackMessage("");
    setFeedbackError("");
    if (feedbackJudgement === "should_use") {
      onToggleFeedback?.("should_use");
      return;
    }
    setReasonOpen(false);
    setReason("");
    onToggleFeedback?.("should_use");
    if (!onSubmitUseful) {
      return;
    }
    setSaving(true);
    try {
      await onSubmitUseful();
      setFeedbackMessage("已记录可用反馈。");
    } catch (error) {
      setFeedbackError(error instanceof Error ? error.message : "无法保存反馈。");
    } finally {
      setSaving(false);
    }
  }

  // 点击“不该用”立即选中（单选互斥，自动取消“应该用”）并展开理由输入；
  // 已勾选时再点则取消本地判定并收起理由框，已落地的 bad case 不撤回。
  function handleNotUsefulToggle() {
    setFeedbackMessage("");
    setFeedbackError("");
    if (feedbackJudgement === "should_not_use") {
      setReasonOpen(false);
      setReason("");
      onToggleFeedback?.("should_not_use");
      return;
    }
    setPreviousJudgement(feedbackJudgement ?? null);
    onToggleFeedback?.("should_not_use");
    setReasonOpen(true);
  }

  // 取消理由输入：收起表单并恢复打开前的判定。
  function handleReasonCancel() {
    setReasonOpen(false);
    setReason("");
    setFeedbackError("");
    if (previousJudgement === "should_use") {
      onToggleFeedback?.("should_use");
    } else {
      onToggleFeedback?.("should_not_use");
    }
  }

  async function handleNotUsefulSubmit() {
    const trimmed = reason.trim();
    if (!trimmed || !onSubmitNotUseful) {
      return;
    }
    setSaving(true);
    setFeedbackError("");
    try {
      await onSubmitNotUseful(trimmed);
      setReasonOpen(false);
      setReason("");
      setFeedbackMessage("已记录不可用反馈。");
    } catch (error) {
      setFeedbackError(
        error instanceof Error ? error.message : "无法保存反馈。"
      );
    } finally {
      setSaving(false);
    }
  }

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
    <article className="evidence-detail" aria-label={`证据 ${index + 1} 明细`}>
      <div className="evidence-header">
        <strong>
          证据 {index + 1} · {formatChunkType(evidence.chunk_type)}
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
            <span className="evidence-boost-badge">标签提权 {boostLabel}</span>
          )}
          {matchedTags.map((tag) => (
            <span className="evidence-tag-chip" key={tag}>
              {tag}
            </span>
          ))}
        </div>
      )}
      {hasStructuredFields(textSegments) ? (
        <EvidenceStructuredText segments={textSegments} />
      ) : (
        <p className="evidence-text">{text}</p>
      )}
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
      {onToggleFeedback && (
        <div
          className="evidence-feedback-actions"
          aria-label={`证据 ${index + 1} 打标`}
        >
          <label>
            <input
              type="checkbox"
              aria-label={`证据 ${index + 1} 应该用`}
              checked={feedbackJudgement === "should_use"}
              disabled={saving}
              onChange={() => void handleUsefulToggle()}
            />
            <span>应该用</span>
          </label>
          <label>
            <input
              type="checkbox"
              aria-label={`证据 ${index + 1} 不该用`}
              checked={feedbackJudgement === "should_not_use"}
              disabled={saving}
              onChange={handleNotUsefulToggle}
            />
            <span>不该用</span>
          </label>
        </div>
      )}
      {reasonOpen && (
        <div className="evidence-not-useful-form">
          <label className="text-field">
            <span>不可用理由</span>
            <textarea
              rows={3}
              value={reason}
              onChange={(event) => {
                setReason(event.target.value);
                setFeedbackError("");
              }}
              placeholder="例如：该证据与客户问题无关，不应作为回答依据。"
            />
          </label>
          <div className="evidence-not-useful-actions">
            <button
              className="secondary-button compact-button"
              type="button"
              disabled={saving || !reason.trim()}
              onClick={() => void handleNotUsefulSubmit()}
            >
              {saving ? (
                <LoaderCircle className="spin" size={16} aria-hidden="true" />
              ) : (
                <Save size={16} aria-hidden="true" />
              )}
              保存不可用反馈
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
          {feedbackError && <p className="form-error">{feedbackError}</p>}
        </div>
      )}
      {feedbackMessage && <p className="success-text">{feedbackMessage}</p>}
    </article>
  );
}
