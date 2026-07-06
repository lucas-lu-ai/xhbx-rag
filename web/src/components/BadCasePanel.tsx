import { CheckCircle2, Flag, LoaderCircle, Save } from "lucide-react";
import { type FormEvent, useState } from "react";

import { submitBadCase } from "../api";
import { formatEvidenceMeta } from "../format";
import type {
  AnswerResponse,
  BadCaseFeedbackResult,
  BadCaseIssueType,
  BadCaseProblemTag,
  BadCaseRequest,
  ChatTurn,
  Citation,
  EvidenceFeedback,
  EvidenceFeedbackJudgement,
  RetrievalEvidence
} from "../types";
import { citedEvidenceIndexes, EvidenceList } from "./EvidenceList";

const feedbackResultOptions: Array<{
  value: BadCaseFeedbackResult;
  label: string;
  tone: "positive" | "negative";
}> = [
  { value: "usable", label: "可用", tone: "positive" },
  { value: "inaccurate", label: "不准确", tone: "negative" },
  { value: "incomplete", label: "不完整", tone: "negative" },
  { value: "citation_issue", label: "引用有问题", tone: "negative" },
  { value: "customer_mismatch", label: "不适合当前客户", tone: "negative" }
];

const problemTagOptions: Array<{ value: BadCaseProblemTag; label: string }> = [
  { value: "off_topic", label: "答非所问" },
  { value: "missing_talk_track", label: "缺关键话术" },
  { value: "case_mismatch", label: "案例不匹配" },
  { value: "citation_mismatch", label: "引用/原文对不上" },
  { value: "not_customer_ready", label: "表达不能直接给客户用" },
  { value: "compliance_risk", label: "可能有合规风险" },
  { value: "other", label: "其他" }
];

type BadCasePanelProps = {
  turn: ChatTurn;
  response: AnswerResponse;
  // 反馈提交入口可注入：聊天视图走 /api/bad-cases，批量视图走行级单入口。
  submit?: (payload: BadCaseRequest) => Promise<unknown>;
  onSavedBadCase?: (payload: BadCaseRequest) => void;
  initiallySubmitted?: boolean;
  // 证据卡片内引用点击联动溯源面板；不传时引用只读展示。
  selectedCitationKey?: string | null;
  onSelectCitation?: (citation: Citation, key: string) => void;
};

export function BadCasePanel({
  turn,
  response,
  submit,
  onSavedBadCase,
  initiallySubmitted = false,
  selectedCitationKey = null,
  onSelectCitation
}: BadCasePanelProps) {
  const [selectedResult, setSelectedResult] =
    useState<BadCaseFeedbackResult | null>(null);
  const [problemTags, setProblemTags] = useState<BadCaseProblemTag[]>([]);
  const [problemDetail, setProblemDetail] = useState("");
  const [expectedAnswer, setExpectedAnswer] = useState("");
  const [referenceNote, setReferenceNote] = useState("");
  const [evidenceFeedback, setEvidenceFeedback] = useState<
    Record<string, EvidenceFeedback>
  >({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(initiallySubmitted);
  const evidences = response.retrieval_evidences ?? [];
  const showForm = selectedResult !== null && selectedResult !== "usable";
  const submitFeedback = submit ?? ((payload: BadCaseRequest) => submitBadCase(payload));

  function toggleProblemTag(value: BadCaseProblemTag) {
    setProblemTags((items) =>
      items.includes(value)
        ? items.filter((item) => item !== value)
        : [...items, value]
    );
    setError("");
    setMessage("");
  }

  function toggleEvidenceFeedback(
    index: number,
    evidence: RetrievalEvidence,
    judgement: EvidenceFeedbackJudgement
  ) {
    const key = evidenceFeedbackKey(index, evidence);
    setEvidenceFeedback((items) => {
      if (items[key]?.judgement === judgement) {
        const { [key]: _removed, ...rest } = items;
        return rest;
      }
      return {
        ...items,
        [key]: {
          chunk_id: evidence.chunk_id,
          judgement,
          label: evidenceFeedbackLabel(index, evidence),
          text_preview: evidenceFeedbackPreview(evidence)
        }
      };
    });
    setError("");
    setMessage("");
  }

  async function saveFeedback(
    feedbackResult: BadCaseFeedbackResult,
    draft = {
      problemTags,
      problemDetail: problemDetail.trim(),
      expectedAnswer: expectedAnswer.trim(),
      referenceNote: referenceNote.trim(),
      evidenceFeedback: Object.values(evidenceFeedback)
    }
  ) {
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const issueTypes = Array.from(
        new Set<BadCaseIssueType>([feedbackResult, ...draft.problemTags])
      );
      const payload: BadCaseRequest = {
        query: turn.query,
        rewritten_query: response.rewritten_query ?? "",
        answer: response.answer,
        top_n: turn.top_n,
        top_k: turn.top_k,
        feedback_result: feedbackResult,
        problem_tags: draft.problemTags,
        problem_detail: draft.problemDetail,
        expected_answer: draft.expectedAnswer,
        reference_note: draft.referenceNote,
        evidence_feedback: draft.evidenceFeedback,
        issue_types: issueTypes,
        expected_knowledge: draft.expectedAnswer,
        expected_source: draft.referenceNote,
        note: draft.problemDetail,
        citations: response.citations,
        retrieval_evidences: evidences
      };
      await submitFeedback(payload);
      if (feedbackResult !== "usable") {
        onSavedBadCase?.(payload);
      }
      setMessage(
        feedbackResult === "usable" ? "已记录可用反馈。" : "反馈已保存。"
      );
      setSubmitted(true);
    } catch (submitError) {
      setError(
        submitError instanceof Error ? submitError.message : "无法保存反馈。"
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleFeedbackResultClick(result: BadCaseFeedbackResult) {
    setSelectedResult(result);
    setError("");
    setMessage("");
    if (result === "usable") {
      setProblemTags([]);
      setProblemDetail("");
      setExpectedAnswer("");
      setReferenceNote("");
      // “可用”也带上已勾选的证据判定，逐证据信号不因整体结论丢失。
      await saveFeedback("usable", {
        problemTags: [],
        problemDetail: "",
        expectedAnswer: "",
        referenceNote: "",
        evidenceFeedback: Object.values(evidenceFeedback)
      });
    }
  }

  async function handleBadCaseSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedResult || selectedResult === "usable") {
      return;
    }
    await saveFeedback(selectedResult);
  }

  return (
    <section className="bad-case-panel" aria-label="回答反馈">
      {evidences.length > 0 && (
        <EvidenceList
          evidences={evidences}
          keyPrefix={turn.id}
          citedIndexes={citedEvidenceIndexes(response.citations)}
          feedback={evidenceFeedback}
          feedbackKeyOf={evidenceFeedbackKey}
          onToggleFeedback={toggleEvidenceFeedback}
          feedbackDisabled={submitted || saving}
          selectedCitationKey={selectedCitationKey}
          onSelectCitation={onSelectCitation}
        />
      )}

      {submitted ? (
        <p className="success-text">{message || "已保存过反馈。"}</p>
      ) : (
        <>
          <div className="answer-feedback">
            <span>这个回答可用吗？</span>
            <div className="answer-feedback-actions">
              {feedbackResultOptions.map((option) => (
                <button
                  className={
                    selectedResult === option.value
                      ? `feedback-option ${option.tone} selected`
                      : `feedback-option ${option.tone}`
                  }
                  key={option.value}
                  type="button"
                  aria-pressed={selectedResult === option.value}
                  disabled={saving}
                  onClick={() => void handleFeedbackResultClick(option.value)}
                >
                  {option.value === "usable" ? (
                    <CheckCircle2 size={15} aria-hidden="true" />
                  ) : (
                    <Flag size={15} aria-hidden="true" />
                  )}
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          {showForm && (
            <form className="bad-case-form" onSubmit={handleBadCaseSubmit}>
              <div className="bad-case-form-heading">
                <strong>反馈这次回答</strong>
                <span>
                  问题、答案、引用、检索证据和上方的逐证据判定会自动随反馈保存。
                </span>
              </div>

              <fieldset className="bad-case-fieldset">
                <legend>问题点</legend>
                <div className="bad-case-option-list">
                  {problemTagOptions.map((option) => (
                    <label className="bad-case-option" key={option.value}>
                      <input
                        type="checkbox"
                        checked={problemTags.includes(option.value)}
                        onChange={() => toggleProblemTag(option.value)}
                      />
                      <span>{option.label}</span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <div className="bad-case-grid">
                <label className="text-field">
                  <span>哪里不对</span>
                  <textarea
                    rows={3}
                    value={problemDetail}
                    onChange={(event) => {
                      setProblemDetail(event.target.value);
                      setMessage("");
                    }}
                    placeholder="例如：回答没有讲清客户为什么要先看保障缺口。"
                  />
                </label>
                <label className="text-field">
                  <span>正确回答应包含什么</span>
                  <textarea
                    rows={3}
                    value={expectedAnswer}
                    onChange={(event) => {
                      setExpectedAnswer(event.target.value);
                      setMessage("");
                    }}
                    placeholder="例如：应该包含保障缺口分析、预算承接和缴费期调整话术。"
                  />
                </label>
              </div>

              <label className="text-field">
                <span>相关案例/章节/文件名</span>
                <input
                  type="text"
                  value={referenceNote}
                  onChange={(event) => {
                    setReferenceNote(event.target.value);
                    setMessage("");
                  }}
                  placeholder="例如：案例A 第3节，或客户预算异议处理案例。"
                />
              </label>

              <div className="bad-case-actions">
                <button
                  className="secondary-button compact-button"
                  type="submit"
                  disabled={saving}
                >
                  {saving ? (
                    <LoaderCircle className="spin" size={16} aria-hidden="true" />
                  ) : (
                    <Save size={16} aria-hidden="true" />
                  )}
                  保存反馈
                </button>
                <span className="bad-case-context">
                  已自动包含 {evidences.length} 条检索证据
                </span>
              </div>
              {error && <p className="form-error">{error}</p>}
              {message && <p className="success-text">{message}</p>}
            </form>
          )}
          {!showForm && error && <p className="form-error">{error}</p>}
          {!showForm && message && <p className="success-text">{message}</p>}
        </>
      )}
    </section>
  );
}

function evidenceFeedbackKey(index: number, evidence: RetrievalEvidence): string {
  return evidence.chunk_id || `evidence-${index}`;
}

function evidenceFeedbackLabel(
  index: number,
  evidence: RetrievalEvidence
): string {
  return formatEvidenceMeta(evidence.metadata) || `证据 ${index + 1}`;
}

function evidenceFeedbackPreview(evidence: RetrievalEvidence): string {
  const text = evidence.text_preview || evidence.text || "没有正文内容。";
  return text.length > 80 ? `${text.slice(0, 80)}...` : text;
}
