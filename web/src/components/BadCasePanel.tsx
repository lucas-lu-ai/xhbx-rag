import { useState } from "react";
import { createPortal } from "react-dom";

import { submitBadCase } from "../api";
import { formatEvidenceMeta } from "../format";
import type {
  AnswerResponse,
  BadCaseRequest,
  ChatTurn,
  EvidenceFeedback,
  EvidenceFeedbackJudgement,
  RetrievalEvidence
} from "../types";
import { EvidenceDetail } from "./EvidenceDetail";
import {
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

// 检索证据面板：紧凑证据列表 + 右侧证据明细。
// 回答级整体反馈已下线，反馈只在证据明细里以“不该用 + 理由”落地 bad case。
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
  const evidences = response.retrieval_evidences ?? [];
  const submitFeedback =
    submit ?? ((payload: BadCaseRequest) => submitBadCase(payload));
  const citedIndexes = citedEvidenceIndexes(response.citations);
  // 选中证据属于本轮问答时，把明细 portal 到右侧面板；
  // 打标状态留在本组件，明细里的判定操作直接读写同一份反馈。
  const selectedEvidenceIndex = evidenceIndexForPrefix(
    selectedEvidenceKey,
    turn.id
  );
  const selectedEvidence =
    selectedEvidenceIndex !== null ? evidences[selectedEvidenceIndex] : undefined;

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
  }

  // 证据“应该用”即时落地：生成一条正向 bad case（feedback_result=usable，
  // 不进 bad case JSONL 导出），成功后同步本地判定。
  async function submitEvidenceUseful(
    index: number,
    evidence: RetrievalEvidence
  ) {
    const entry: EvidenceFeedback = {
      chunk_id: evidence.chunk_id,
      judgement: "should_use",
      label: evidenceFeedbackLabel(index, evidence),
      text_preview: evidenceFeedbackPreview(evidence)
    };
    const payload: BadCaseRequest = {
      query: turn.query,
      rewritten_query: response.rewritten_query ?? "",
      answer: response.answer,
      top_n: turn.top_n,
      top_k: turn.top_k,
      feedback_result: "usable",
      problem_tags: [],
      problem_detail: "",
      expected_answer: "",
      reference_note: "",
      evidence_feedback: [entry],
      issue_types: ["usable"],
      expected_knowledge: "",
      expected_source: "",
      note: "",
      citations: response.citations,
      retrieval_evidences: evidences
    };
    await submitFeedback(payload);
    onSavedBadCase?.(payload);
    setEvidenceFeedback((items) => ({
      ...items,
      [evidenceFeedbackKey(index, evidence)]: entry
    }));
  }

  // 证据“不该用”即时落地：带理由生成一条 bad case（批量走行级入口），
  // 成功后同步本地判定，行的“已反馈”状态与导出按钮随之更新。
  async function submitEvidenceNotUseful(
    index: number,
    evidence: RetrievalEvidence,
    reason: string
  ) {
    const entry: EvidenceFeedback = {
      chunk_id: evidence.chunk_id,
      judgement: "should_not_use",
      label: evidenceFeedbackLabel(index, evidence),
      text_preview: evidenceFeedbackPreview(evidence),
      reason
    };
    const payload: BadCaseRequest = {
      query: turn.query,
      rewritten_query: response.rewritten_query ?? "",
      answer: response.answer,
      top_n: turn.top_n,
      top_k: turn.top_k,
      feedback_result: "citation_issue",
      problem_tags: [],
      problem_detail: reason,
      expected_answer: "",
      reference_note: "",
      evidence_feedback: [entry],
      issue_types: ["citation_issue"],
      expected_knowledge: "",
      expected_source: "",
      note: reason,
      citations: response.citations,
      retrieval_evidences: evidences
    };
    await submitFeedback(payload);
    onSavedBadCase?.(payload);
    setEvidenceFeedback((items) => ({
      ...items,
      [evidenceFeedbackKey(index, evidence)]: entry
    }));
  }

  return (
    <section className="bad-case-panel">
      {evidences.length > 0 && (
        <EvidenceList
          evidences={evidences}
          keyPrefix={turn.id}
          citedIndexes={citedIndexes}
          selectedEvidenceKey={selectedEvidenceKey}
          onSelectEvidence={onSelectEvidence}
        />
      )}
      {selectedEvidenceIndex !== null &&
        selectedEvidence &&
        container &&
        createPortal(
          <EvidenceDetail
            key={selectedEvidenceKey}
            evidence={selectedEvidence}
            index={selectedEvidenceIndex}
            cited={citedIndexes.has(selectedEvidenceIndex + 1)}
            feedbackJudgement={
              evidenceFeedback[
                evidenceFeedbackKey(selectedEvidenceIndex, selectedEvidence)
              ]?.judgement
            }
            onToggleFeedback={(judgement) =>
              toggleEvidenceFeedback(
                selectedEvidenceIndex,
                selectedEvidence,
                judgement
              )
            }
            onSubmitUseful={() =>
              submitEvidenceUseful(selectedEvidenceIndex, selectedEvidence)
            }
            onSubmitNotUseful={(reason) =>
              submitEvidenceNotUseful(
                selectedEvidenceIndex,
                selectedEvidence,
                reason
              )
            }
          />,
          container
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
