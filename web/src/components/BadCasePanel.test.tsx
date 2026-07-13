import {
  fireEvent,
  render,
  screen,
  waitFor
} from "@testing-library/react";
import { useState } from "react";

import type {
  AnswerResponse,
  BadCaseRequest,
  ChatTurn,
  EvidenceFeedbackJudgement,
  RetrievalEvidence
} from "../types";
import { BadCasePanel } from "./BadCasePanel";
import { EvidenceDetailContext } from "./EvidenceDetailContext";

vi.mock("./EvidenceDetail", () => ({
  EvidenceDetail: ({
    evidence,
    relatedEvidences,
    index,
    feedbackJudgement,
    onToggleFeedback,
    onSubmitUseful,
    onSubmitNotUseful
  }: {
    evidence: RetrievalEvidence;
    relatedEvidences: RetrievalEvidence[];
    index: number;
    feedbackJudgement?: EvidenceFeedbackJudgement;
    onToggleFeedback: (judgement: EvidenceFeedbackJudgement) => void;
    onSubmitUseful: () => void;
    onSubmitNotUseful: (reason: string) => void;
  }) => (
    <article
      data-testid="mock-evidence-detail"
      data-index={index}
      data-chunk-id={evidence.chunk_id}
      data-related-count={relatedEvidences.length}
    >
      <span data-testid="mock-feedback-judgement">
        {feedbackJudgement ?? ""}
      </span>
      <button
        type="button"
        onClick={() => onToggleFeedback("should_use")}
      >
        切换应该用状态
      </button>
      <button type="button" onClick={onSubmitUseful}>
        提交应该用
      </button>
      <button
        type="button"
        onClick={() => onSubmitNotUseful("引用内容不匹配")}
      >
        提交不该用
      </button>
    </article>
  )
}));

const evidences: RetrievalEvidence[] = [
  { chunk_id: "chunk-1", text: "第一条证据" },
  { chunk_id: "chunk-2", text: "第二条证据" },
  { chunk_id: "chunk-3", text: "第三条证据" }
];

const response: AnswerResponse = {
  answer: "回答",
  citations: [
    {
      display_location: "证据 1",
      display_excerpt: "第一条证据",
      can_reveal: false,
      selected: true,
      evidence_index: 1
    },
    {
      display_location: "证据 3",
      display_excerpt: "第三条证据",
      can_reveal: false,
      selected: true,
      evidence_index: 3
    }
  ],
  evidence_count: evidences.length,
  retrieval_evidences: evidences
};

const turn: ChatTurn = {
  id: "turn-1",
  query: "测试问题",
  top_n: 10,
  top_k: 3
};

const unkeyedEvidences: RetrievalEvidence[] = [
  { text: "未引用的第一条证据" },
  { text: "被引用的第二条证据" },
  { text: "被引用的第三条证据" }
];

const adjacentCitationResponse: AnswerResponse = {
  answer: "相邻引用回答",
  citations: [
    {
      display_location: "证据 2",
      display_excerpt: "被引用的第二条证据",
      can_reveal: false,
      selected: true,
      evidence_index: 2
    },
    {
      display_location: "证据 3",
      display_excerpt: "被引用的第三条证据",
      can_reveal: false,
      selected: true,
      evidence_index: 3
    }
  ],
  evidence_count: unkeyedEvidences.length,
  retrieval_evidences: unkeyedEvidences
};

const duplicateChunkEvidences: RetrievalEvidence[] = [
  { chunk_id: "unreferenced", text: "未引用的第一条证据" },
  { chunk_id: "duplicate-chunk", text: "同 chunk 的第二条证据" },
  { chunk_id: "duplicate-chunk", text: "同 chunk 的第三条证据" }
];

const duplicateChunkResponse: AnswerResponse = {
  ...adjacentCitationResponse,
  evidence_count: duplicateChunkEvidences.length,
  retrieval_evidences: duplicateChunkEvidences
};

afterEach(() => {
  document
    .querySelectorAll("[data-testid='evidence-portal']")
    .forEach((container) => container.remove());
});

function renderPanel(selectedEvidenceKey: string) {
  const portalContainer = document.createElement("div");
  portalContainer.dataset.testid = "evidence-portal";
  document.body.appendChild(portalContainer);
  const submit = vi.fn().mockResolvedValue({});

  render(
    <EvidenceDetailContext.Provider
      value={{
        container: portalContainer,
        selectedEvidenceKey,
        onSelectEvidence: vi.fn()
      }}
    >
      <BadCasePanel turn={turn} response={response} submit={submit} />
    </EvidenceDetailContext.Provider>
  );

  return { portalContainer, submit };
}

function FeedbackSelectionHarness({
  portalContainer,
  response: panelResponse = adjacentCitationResponse
}: {
  portalContainer: HTMLElement;
  response?: AnswerResponse;
}) {
  const [selectedEvidenceKey, setSelectedEvidenceKey] = useState<string | null>(
    "turn-1:evidence-2"
  );

  return (
    <>
      <button
        type="button"
        onClick={() => setSelectedEvidenceKey("turn-1:evidence-1")}
      >
        查看原始第 2 条
      </button>
      <button
        type="button"
        onClick={() => setSelectedEvidenceKey("turn-1:evidence-2")}
      >
        查看原始第 3 条
      </button>
      <EvidenceDetailContext.Provider
        value={{
          container: portalContainer,
          selectedEvidenceKey,
          onSelectEvidence: setSelectedEvidenceKey
        }}
      >
        <BadCasePanel
          turn={turn}
          response={panelResponse}
          submit={vi.fn().mockResolvedValue({})}
        />
      </EvidenceDetailContext.Provider>
    </>
  );
}

test("引用详情使用连续显示索引，同时保留原始证据与完整关联证据", async () => {
  const { portalContainer, submit } = renderPanel("turn-1:evidence-2");

  const detail = screen.getByTestId("mock-evidence-detail");
  expect(portalContainer).toContainElement(detail);
  expect(detail).toHaveAttribute("data-index", "1");
  expect(detail).toHaveAttribute("data-chunk-id", "chunk-3");
  expect(detail).toHaveAttribute("data-related-count", "3");

  fireEvent.click(screen.getByRole("button", { name: "提交应该用" }));

  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  const payload = submit.mock.calls[0][0] as BadCaseRequest;
  expect(payload.retrieval_evidences).toEqual(evidences);
  expect(payload.evidence_feedback).toEqual([
    expect.objectContaining({
      chunk_id: "chunk-3",
      judgement: "should_use",
      label: "证据 3"
    })
  ]);
});

test("选中未引用的原始证据时不 portal 详情", () => {
  renderPanel("turn-1:evidence-1");

  expect(screen.queryByTestId("mock-evidence-detail")).not.toBeInTheDocument();
});

test("反馈 toggle 按原始 evidence 索引隔离相邻引用状态", () => {
  const portalContainer = document.createElement("div");
  portalContainer.dataset.testid = "evidence-portal";
  document.body.appendChild(portalContainer);
  render(<FeedbackSelectionHarness portalContainer={portalContainer} />);

  expect(
    screen.getByTestId("mock-feedback-judgement")
  ).toBeEmptyDOMElement();
  fireEvent.click(
    screen.getByRole("button", { name: "切换应该用状态" })
  );
  expect(screen.getByTestId("mock-feedback-judgement")).toHaveTextContent(
    "should_use"
  );

  fireEvent.click(screen.getByRole("button", { name: "查看原始第 2 条" }));
  expect(
    screen.getByTestId("mock-feedback-judgement")
  ).toBeEmptyDOMElement();

  fireEvent.click(screen.getByRole("button", { name: "查看原始第 3 条" }));
  expect(screen.getByTestId("mock-feedback-judgement")).toHaveTextContent(
    "should_use"
  );
});

test("重复 chunk_id 的引用反馈仍按原始 evidence 索引隔离", () => {
  const portalContainer = document.createElement("div");
  portalContainer.dataset.testid = "evidence-portal";
  document.body.appendChild(portalContainer);
  render(
    <FeedbackSelectionHarness
      portalContainer={portalContainer}
      response={duplicateChunkResponse}
    />
  );

  fireEvent.click(
    screen.getByRole("button", { name: "切换应该用状态" })
  );
  expect(screen.getByTestId("mock-feedback-judgement")).toHaveTextContent(
    "should_use"
  );

  fireEvent.click(screen.getByRole("button", { name: "查看原始第 2 条" }));
  expect(
    screen.getByTestId("mock-feedback-judgement")
  ).toBeEmptyDOMElement();

  fireEvent.click(screen.getByRole("button", { name: "查看原始第 3 条" }));
  expect(screen.getByTestId("mock-feedback-judgement")).toHaveTextContent(
    "should_use"
  );
});
