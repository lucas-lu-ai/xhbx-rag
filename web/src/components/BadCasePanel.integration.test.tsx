import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import type {
  AnswerResponse,
  BadCaseRequest,
  ChatTurn
} from "../types";
import { BadCasePanel } from "./BadCasePanel";
import { EvidenceDetailContext } from "./EvidenceDetailContext";

const turn: ChatTurn = {
  id: "turn-race",
  query: "测试竞态",
  top_n: 10,
  top_k: 3
};

const response: AnswerResponse = {
  answer: "测试回答",
  citations: [
    {
      display_location: "证据 1",
      display_excerpt: "证据 A",
      can_reveal: false,
      selected: true,
      evidence_index: 1
    },
    {
      display_location: "证据 2",
      display_excerpt: "证据 B",
      can_reveal: false,
      selected: true,
      evidence_index: 2
    }
  ],
  evidence_count: 2,
  retrieval_evidences: [
    { chunk_id: "chunk-a", text: "证据 A" },
    { chunk_id: "chunk-b", text: "证据 B" }
  ]
};

type Deferred = {
  promise: Promise<unknown>;
  resolve: (value?: unknown) => void;
  reject: (error: Error) => void;
};

function deferred(): Deferred {
  let resolve!: (value?: unknown) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<unknown>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function SelectionHarness({
  portalContainer,
  submit,
  onSavedBadCase
}: {
  portalContainer: HTMLElement;
  submit: (payload: BadCaseRequest) => Promise<unknown>;
  onSavedBadCase: (payload: BadCaseRequest) => void;
}) {
  const [selectedEvidenceKey, setSelectedEvidenceKey] = useState<string | null>(
    "turn-race:evidence-0"
  );

  return (
    <>
      <button
        type="button"
        onClick={() => setSelectedEvidenceKey("turn-race:evidence-1")}
      >
        选择 B
      </button>
      <button
        type="button"
        onClick={() => setSelectedEvidenceKey("turn-race:evidence-0")}
      >
        返回 A
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
          response={response}
          submit={submit}
          onSavedBadCase={onSavedBadCase}
        />
      </EvidenceDetailContext.Provider>
    </>
  );
}

function renderRaceHarness(
  submit: (payload: BadCaseRequest) => Promise<unknown>,
  onSavedBadCase: (payload: BadCaseRequest) => void
) {
  const portalContainer = document.createElement("div");
  document.body.appendChild(portalContainer);
  const result = render(
    <SelectionHarness
      portalContainer={portalContainer}
      submit={submit}
      onSavedBadCase={onSavedBadCase}
    />
  );
  return {
    ...result,
    unmount() {
      result.unmount();
      portalContainer.remove();
    }
  };
}

async function startPositiveA(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByLabelText("引用1召回准确"));
  await user.click(screen.getByLabelText("引用1参考正确"));
}

async function returnToAAndStartNegativeA(
  user: ReturnType<typeof userEvent.setup>
) {
  await user.click(screen.getByRole("button", { name: "选择 B" }));
  await user.click(screen.getByRole("button", { name: "返回 A" }));
  await user.click(screen.getByLabelText("引用1召回不准确"));
  await user.type(screen.getByLabelText("召回不准确原因"), "A2 新原因");
  await user.click(screen.getByRole("button", { name: "保存反馈" }));
}

test("A→B→A 后旧 A1 成功不会通知或覆盖当前 A2", async () => {
  const user = userEvent.setup();
  const a1 = deferred();
  const a2 = deferred();
  const submit = vi
    .fn<(payload: BadCaseRequest) => Promise<unknown>>()
    .mockImplementationOnce(() => a1.promise)
    .mockImplementationOnce(() => a2.promise);
  const onSavedBadCase = vi.fn<(payload: BadCaseRequest) => void>();
  const view = renderRaceHarness(submit, onSavedBadCase);

  await startPositiveA(user);
  await returnToAAndStartNegativeA(user);
  expect(submit).toHaveBeenCalledTimes(2);

  await act(async () => {
    a1.resolve({});
    await a1.promise;
  });

  expect(onSavedBadCase).not.toHaveBeenCalled();
  expect(screen.getByLabelText("引用1召回不准确")).toBeChecked();
  expect(screen.getByLabelText("引用1召回不准确")).toBeDisabled();
  expect(screen.getByLabelText("召回不准确原因")).toHaveValue("A2 新原因");

  await act(async () => {
    a2.resolve({});
    await a2.promise;
  });

  expect(onSavedBadCase).toHaveBeenCalledTimes(1);
  expect(onSavedBadCase.mock.calls[0][0]).toMatchObject({
    feedback_result: "citation_issue",
    evidence_feedback: [
      expect.objectContaining({
        chunk_id: "chunk-a",
        retrieval_judgement: "inaccurate",
        reason: "A2 新原因"
      })
    ]
  });
  expect(await screen.findByText("已记录引用反馈。")).toBeInTheDocument();
  expect(screen.getByLabelText("引用1召回不准确")).toBeChecked();
  expect(screen.getByLabelText("引用1召回不准确")).toBeDisabled();
  view.unmount();
});

test("A→B→A 后旧 A1 失败不会破坏当前 A2", async () => {
  const user = userEvent.setup();
  const a1 = deferred();
  const a2 = deferred();
  const submit = vi
    .fn<(payload: BadCaseRequest) => Promise<unknown>>()
    .mockImplementationOnce(() => a1.promise)
    .mockImplementationOnce(() => a2.promise);
  const onSavedBadCase = vi.fn<(payload: BadCaseRequest) => void>();
  const view = renderRaceHarness(submit, onSavedBadCase);

  await startPositiveA(user);
  await returnToAAndStartNegativeA(user);

  await act(async () => {
    a1.reject(new Error("旧 A1 失败"));
    await expect(a1.promise).rejects.toThrow("旧 A1 失败");
  });

  expect(onSavedBadCase).not.toHaveBeenCalled();
  expect(screen.queryByText("旧 A1 失败")).not.toBeInTheDocument();
  expect(screen.getByLabelText("引用1召回不准确")).toBeChecked();
  expect(screen.getByLabelText("召回不准确原因")).toHaveValue("A2 新原因");

  await act(async () => {
    a2.resolve({});
    await a2.promise;
  });
  expect(onSavedBadCase).toHaveBeenCalledTimes(1);
  expect(await screen.findByText("已记录引用反馈。")).toBeInTheDocument();
  view.unmount();
});

test("onSavedBadCase 同步异常不会让真实反馈表单表现为提交失败", async () => {
  const user = userEvent.setup();
  const submit = vi
    .fn<(payload: BadCaseRequest) => Promise<unknown>>()
    .mockResolvedValue({});
  const onSavedBadCase = vi.fn<(payload: BadCaseRequest) => void>(() => {
    throw new Error("通知失败");
  });
  const view = renderRaceHarness(submit, onSavedBadCase);

  await startPositiveA(user);

  expect(await screen.findByText("已记录引用反馈。")).toBeInTheDocument();
  expect(screen.queryByText("通知失败")).not.toBeInTheDocument();
  expect(onSavedBadCase).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("引用1召回准确")).toBeChecked();
  expect(screen.getByLabelText("引用1召回准确")).toBeDisabled();
  expect(screen.getByLabelText("引用1参考正确")).toBeChecked();
  expect(screen.getByLabelText("引用1参考正确")).toBeDisabled();
  view.unmount();
});
