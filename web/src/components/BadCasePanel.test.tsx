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
  EvidenceFeedback,
  EvidenceFeedbackDecision,
  RetrievalEvidence
} from "../types";
import { BadCasePanel } from "./BadCasePanel";
import { EvidenceDetailContext } from "./EvidenceDetailContext";

vi.mock("./EvidenceDetail", () => ({
  EvidenceDetail: ({
    evidence,
    relatedEvidences,
    index,
    feedback,
    onSubmitFeedback
  }: {
    evidence: RetrievalEvidence;
    relatedEvidences: RetrievalEvidence[];
    index: number;
    feedback?: EvidenceFeedback;
    onSubmitFeedback: (decision: EvidenceFeedbackDecision) => Promise<void>;
  }) => (
    <article
      data-testid="mock-evidence-detail"
      data-index={index}
      data-chunk-id={evidence.chunk_id}
      data-related-count={relatedEvidences.length}
    >
      <span data-testid="mock-feedback">
        {feedback ? JSON.stringify(feedback) : ""}
      </span>
      <button
        type="button"
        onClick={() =>
          void onSubmitFeedback({
            retrieval_judgement: "accurate",
            answer_usage_judgement: "correct"
          }).catch(() => undefined)
        }
      >
        提交准确且参考正确
      </button>
      <button
        type="button"
        onClick={() =>
          void onSubmitFeedback({
            retrieval_judgement: "inaccurate",
            answer_usage_judgement: "not_applicable",
            reason: "  引用内容不匹配  "
          }).catch(() => undefined)
        }
      >
        提交召回不准确
      </button>
      <button
        type="button"
        onClick={() =>
          void onSubmitFeedback({
            retrieval_judgement: "accurate",
            answer_usage_judgement: "incorrect",
            reason: "  回答超出证据范围  "
          }).catch(() => undefined)
        }
      >
        提交参考不正确
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

function renderPanel(
  selectedEvidenceKey: string,
  options: {
    submit?: ReturnType<
      typeof vi.fn<(payload: BadCaseRequest) => Promise<unknown>>
    >;
    onSavedBadCase?: ReturnType<
      typeof vi.fn<(payload: BadCaseRequest) => void>
    >;
  } = {}
) {
  const portalContainer = document.createElement("div");
  portalContainer.dataset.testid = "evidence-portal";
  document.body.appendChild(portalContainer);
  const submit =
    options.submit ??
    vi.fn<(payload: BadCaseRequest) => Promise<unknown>>().mockResolvedValue({});

  render(
    <EvidenceDetailContext.Provider
      value={{
        container: portalContainer,
        selectedEvidenceKey,
        onSelectEvidence: vi.fn()
      }}
    >
      <BadCasePanel
        turn={turn}
        response={response}
        submit={submit}
        onSavedBadCase={options.onSavedBadCase}
      />
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

test("准确且参考正确映射为 usable，并保留完整证据", async () => {
  const { portalContainer, submit } = renderPanel("turn-1:evidence-2");

  const detail = screen.getByTestId("mock-evidence-detail");
  expect(portalContainer).toContainElement(detail);
  expect(detail).toHaveAttribute("data-index", "1");
  expect(detail).toHaveAttribute("data-chunk-id", "chunk-3");
  expect(detail).toHaveAttribute("data-related-count", "3");

  fireEvent.click(
    screen.getByRole("button", { name: "提交准确且参考正确" })
  );

  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  const payload = submit.mock.calls[0][0] as BadCaseRequest;
  expect(payload).toEqual({
    query: "测试问题",
    rewritten_query: "",
    answer: "回答",
    top_n: 10,
    top_k: 3,
    feedback_result: "usable",
    problem_tags: [],
    issue_types: ["usable"],
    problem_detail: "",
    expected_answer: "",
    reference_note: "",
    expected_knowledge: "",
    expected_source: "",
    note: "",
    citations: response.citations,
    retrieval_evidences: evidences,
    evidence_feedback: [{
      chunk_id: "chunk-3",
      retrieval_judgement: "accurate",
      answer_usage_judgement: "correct",
      label: "证据 3",
      text_preview: "第三条证据"
    }]
  });
});

test("召回不准确映射为 citation_issue 并 trim 理由", async () => {
  const { submit } = renderPanel("turn-1:evidence-2");

  fireEvent.click(screen.getByRole("button", { name: "提交召回不准确" }));

  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  expect(submit.mock.calls[0][0]).toEqual({
    query: "测试问题",
    rewritten_query: "",
    answer: "回答",
    top_n: 10,
    top_k: 3,
    feedback_result: "citation_issue",
    problem_tags: [],
    issue_types: ["citation_wrong"],
    problem_detail: "引用内容不匹配",
    expected_answer: "",
    reference_note: "",
    expected_knowledge: "",
    expected_source: "",
    note: "引用内容不匹配",
    citations: response.citations,
    retrieval_evidences: evidences,
    evidence_feedback: [{
      chunk_id: "chunk-3",
      retrieval_judgement: "inaccurate",
      answer_usage_judgement: "not_applicable",
      reason: "引用内容不匹配",
      label: "证据 3",
      text_preview: "第三条证据"
    }]
  });
});

test("召回准确但参考不正确映射为 inaccurate 并 trim 理由", async () => {
  const { submit } = renderPanel("turn-1:evidence-2");

  fireEvent.click(screen.getByRole("button", { name: "提交参考不正确" }));

  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  expect(submit.mock.calls[0][0]).toEqual({
    query: "测试问题",
    rewritten_query: "",
    answer: "回答",
    top_n: 10,
    top_k: 3,
    feedback_result: "inaccurate",
    problem_tags: [],
    issue_types: ["answer_unsupported"],
    problem_detail: "回答超出证据范围",
    expected_answer: "",
    reference_note: "",
    expected_knowledge: "",
    expected_source: "",
    note: "回答超出证据范围",
    citations: response.citations,
    retrieval_evidences: evidences,
    evidence_feedback: [{
      chunk_id: "chunk-3",
      retrieval_judgement: "accurate",
      answer_usage_judgement: "incorrect",
      reason: "回答超出证据范围",
      label: "证据 3",
      text_preview: "第三条证据"
    }]
  });
});

test("提交失败时不回填 feedback 且不通知已保存", async () => {
  const submit = vi
    .fn<(payload: BadCaseRequest) => Promise<unknown>>()
    .mockRejectedValue(new Error("保存失败"));
  const onSavedBadCase = vi.fn<(payload: BadCaseRequest) => void>();
  renderPanel("turn-1:evidence-2", { submit, onSavedBadCase });

  fireEvent.click(
    screen.getByRole("button", { name: "提交准确且参考正确" })
  );

  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  expect(screen.getByTestId("mock-feedback")).toBeEmptyDOMElement();
  expect(onSavedBadCase).not.toHaveBeenCalled();
});

test("选中未引用的原始证据时不 portal 详情", () => {
  renderPanel("turn-1:evidence-1");

  expect(screen.queryByTestId("mock-evidence-detail")).not.toBeInTheDocument();
});

test("反馈按原始 evidence 索引隔离相邻引用状态", async () => {
  const portalContainer = document.createElement("div");
  portalContainer.dataset.testid = "evidence-portal";
  document.body.appendChild(portalContainer);
  render(<FeedbackSelectionHarness portalContainer={portalContainer} />);

  expect(
    screen.getByTestId("mock-feedback")
  ).toBeEmptyDOMElement();
  fireEvent.click(
    screen.getByRole("button", { name: "提交准确且参考正确" })
  );
  await waitFor(() =>
    expect(screen.getByTestId("mock-feedback")).toHaveTextContent(
      '"retrieval_judgement":"accurate"'
    )
  );

  fireEvent.click(screen.getByRole("button", { name: "查看原始第 2 条" }));
  expect(
    screen.getByTestId("mock-feedback")
  ).toBeEmptyDOMElement();

  fireEvent.click(screen.getByRole("button", { name: "查看原始第 3 条" }));
  expect(screen.getByTestId("mock-feedback")).toHaveTextContent(
    '"answer_usage_judgement":"correct"'
  );
});

test("重复 chunk_id 的引用反馈仍按原始 evidence 索引隔离", async () => {
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
    screen.getByRole("button", { name: "提交准确且参考正确" })
  );
  await waitFor(() =>
    expect(screen.getByTestId("mock-feedback")).toHaveTextContent(
      '"retrieval_judgement":"accurate"'
    )
  );

  fireEvent.click(screen.getByRole("button", { name: "查看原始第 2 条" }));
  expect(
    screen.getByTestId("mock-feedback")
  ).toBeEmptyDOMElement();

  fireEvent.click(screen.getByRole("button", { name: "查看原始第 3 条" }));
  expect(screen.getByTestId("mock-feedback")).toHaveTextContent(
    '"answer_usage_judgement":"correct"'
  );
});
