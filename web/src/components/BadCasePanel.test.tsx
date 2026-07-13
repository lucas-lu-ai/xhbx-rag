import {
  fireEvent,
  render,
  screen,
  waitFor
} from "@testing-library/react";

import type {
  AnswerResponse,
  BadCaseRequest,
  ChatTurn,
  RetrievalEvidence
} from "../types";
import { BadCasePanel } from "./BadCasePanel";
import { EvidenceDetailContext } from "./EvidenceDetailContext";

vi.mock("./EvidenceDetail", () => ({
  EvidenceDetail: ({
    evidence,
    relatedEvidences,
    index,
    onSubmitUseful,
    onSubmitNotUseful
  }: {
    evidence: RetrievalEvidence;
    relatedEvidences: RetrievalEvidence[];
    index: number;
    onSubmitUseful: () => void;
    onSubmitNotUseful: (reason: string) => void;
  }) => (
    <article
      data-testid="mock-evidence-detail"
      data-index={index}
      data-chunk-id={evidence.chunk_id}
      data-related-count={relatedEvidences.length}
    >
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
