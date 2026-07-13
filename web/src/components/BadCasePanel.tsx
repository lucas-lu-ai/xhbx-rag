import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { submitBadCase } from "../api";
import { formatEvidenceMeta } from "../format";
import type {
  AnswerResponse,
  BadCaseRequest,
  BadCaseFeedbackResult,
  BadCaseIssueType,
  ChatTurn,
  EvidenceFeedback,
  EvidenceFeedbackDecision,
  RetrievalEvidence
} from "../types";
import { EvidenceDetail } from "./EvidenceDetail";
import {
  citedEvidenceEntries,
  citedEvidenceIndexes,
  evidenceIndexForPrefix,
  useEvidenceDetail
} from "./EvidenceDetailContext";
import { EvidenceList } from "./EvidenceList";

type BadCasePanelProps = {
  turn: ChatTurn;
  response: AnswerResponse;
  // 反馈提交入口可注入：聊天视图走 /api/bad-cases，批量视图走行级单入口。
  submit?: (payload: BadCaseRequest) => Promise<unknown>;
  onSavedBadCase?: (payload: BadCaseRequest) => void;
};

// 知识引用面板：紧凑引用列表 + 右侧引用明细。
// 回答级整体反馈已下线，反馈只在引用明细里按召回与回答参考两个维度落地。
export function BadCasePanel({
  turn,
  response,
  submit,
  onSavedBadCase
}: BadCasePanelProps) {
  const { container, selectedEvidenceKey, onSelectEvidence } =
    useEvidenceDetail();
  const [evidenceFeedback, setEvidenceFeedback] = useState<
    Record<string, EvidenceFeedback>
  >({});
  const selectionGenerationRef = useRef(0);
  const feedbackRequestIdRef = useRef(0);
  const activeFeedbackRequestRef = useRef<{
    generation: number;
    requestId: number;
  } | null>(null);
  const evidences = response.retrieval_evidences ?? [];
  const submitFeedback =
    submit ?? ((payload: BadCaseRequest) => submitBadCase(payload));
  const citedIndexes = citedEvidenceIndexes(response.citations);
  const citedEntries = citedEvidenceEntries(evidences, citedIndexes);
  // 选中证据属于本轮问答且被模型实际引用时，把明细 portal 到右侧面板；
  // 打标状态留在本组件，明细里的判定操作直接读写同一份反馈。
  const selectedEvidenceIndex = evidenceIndexForPrefix(
    selectedEvidenceKey,
    turn.id
  );
  const selectedEntry = citedEntries.find(
    ({ evidenceIndex }) => evidenceIndex === selectedEvidenceIndex
  );

  // 每次选择变更都推进单调代次；即使 A→B→A 回到相同 key，旧 A 请求也不会复活。
  // layout effect 在新明细可交互前完成失效，cleanup 同时覆盖卸载与切换间隙。
  useLayoutEffect(() => {
    selectionGenerationRef.current += 1;
    activeFeedbackRequestRef.current = null;
    return () => {
      selectionGenerationRef.current += 1;
      activeFeedbackRequestRef.current = null;
    };
  }, [selectedEvidenceKey]);

  async function submitEvidenceFeedback(
    index: number,
    evidence: RetrievalEvidence,
    decision: EvidenceFeedbackDecision
  ) {
    const generation = selectionGenerationRef.current;
    const requestId = feedbackRequestIdRef.current + 1;
    feedbackRequestIdRef.current = requestId;
    activeFeedbackRequestRef.current = { generation, requestId };
    const isCurrentRequest = () =>
      selectionGenerationRef.current === generation &&
      activeFeedbackRequestRef.current?.generation === generation &&
      activeFeedbackRequestRef.current.requestId === requestId;
    const classification = classifyEvidenceFeedbackDecision(decision);
    const { normalizedDecision, feedbackResult, issueTypes, reason } =
      classification;
    const entry: EvidenceFeedback = {
      chunk_id: evidence.chunk_id,
      ...normalizedDecision,
      label: evidenceFeedbackLabel(index, evidence),
      text_preview: evidenceFeedbackPreview(evidence)
    };
    const payload: BadCaseRequest = {
      query: turn.query,
      rewritten_query: response.rewritten_query ?? "",
      answer: response.answer,
      top_n: turn.top_n,
      top_k: turn.top_k,
      feedback_result: feedbackResult,
      problem_tags: [],
      problem_detail: reason,
      expected_answer: "",
      reference_note: "",
      evidence_feedback: [entry],
      issue_types: issueTypes,
      expected_knowledge: "",
      expected_source: "",
      note: reason,
      citations: response.citations,
      retrieval_evidences: evidences
    };
    try {
      await submitFeedback(payload);
      if (!isCurrentRequest()) {
        return;
      }
      setEvidenceFeedback((items) => ({
        ...items,
        [evidenceFeedbackKey(turn.id, index)]: entry
      }));
      try {
        onSavedBadCase?.(payload);
      } catch {
        // 通知属于远端保存后的旁路同步，不得把已保存反馈重新暴露为可重试。
      }
    } finally {
      if (isCurrentRequest()) {
        activeFeedbackRequestRef.current = null;
      }
    }
  }

  return (
    <section className="bad-case-panel">
      {citedEntries.length > 0 && (
        <EvidenceList
          evidences={evidences}
          keyPrefix={turn.id}
          citedIndexes={citedIndexes}
          selectedEvidenceKey={selectedEvidenceKey}
          onSelectEvidence={onSelectEvidence}
        />
      )}
      {selectedEntry &&
        container &&
        createPortal(
          <EvidenceDetail
            key={selectedEvidenceKey}
            evidence={selectedEntry.evidence}
            index={selectedEntry.displayIndex}
            feedback={
              evidenceFeedback[
                evidenceFeedbackKey(turn.id, selectedEntry.evidenceIndex)
              ]
            }
            onSubmitFeedback={(decision) =>
              submitEvidenceFeedback(
                selectedEntry.evidenceIndex,
                selectedEntry.evidence,
                decision
              )
            }
          />,
          container
        )}
    </section>
  );
}

type EvidenceFeedbackClassification = {
  normalizedDecision: EvidenceFeedbackDecision;
  feedbackResult: BadCaseFeedbackResult;
  issueTypes: BadCaseIssueType[];
  reason: string;
};

function classifyEvidenceFeedbackDecision(
  decision: EvidenceFeedbackDecision
): EvidenceFeedbackClassification {
  switch (decision.retrieval_judgement) {
    case "inaccurate":
      switch (decision.answer_usage_judgement) {
        case "not_applicable": {
          const reason = decision.reason.trim();
          return {
            normalizedDecision: { ...decision, reason },
            feedbackResult: "citation_issue",
            issueTypes: ["citation_wrong"],
            reason
          };
        }
        default:
          return assertNever(decision);
      }
    case "accurate":
      switch (decision.answer_usage_judgement) {
        case "correct":
          return {
            normalizedDecision: decision,
            feedbackResult: "usable",
            issueTypes: ["usable"],
            reason: ""
          };
        case "incorrect": {
          const reason = decision.reason.trim();
          return {
            normalizedDecision: { ...decision, reason },
            feedbackResult: "inaccurate",
            issueTypes: ["answer_unsupported"],
            reason
          };
        }
        default:
          return assertNever(decision);
      }
    default:
      return assertNever(decision);
  }
}

function assertNever(value: never): never {
  throw new Error(`未处理的引用反馈判定：${JSON.stringify(value)}`);
}

function evidenceFeedbackKey(turnId: string, index: number): string {
  return `${turnId}:evidence-index:${index}`;
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
