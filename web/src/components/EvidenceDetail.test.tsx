import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { EvidenceDetail } from "./EvidenceDetail";
import { installFetchStub, runRegisteredCleanups } from "../test-utils";
import type {
  EvidenceFeedback,
  EvidenceFeedbackDecision,
  RetrievalEvidence
} from "../types";

afterEach(() => {
  runRegisteredCleanups();
  vi.unstubAllGlobals();
});

const evidence: RetrievalEvidence = {
  chunk_id: "c1",
  chunk_type: "objection_handling",
  text: "客户担心预算，可以先承接预算，再对齐保障缺口。",
  rerank_score: 0.91,
  matched_tag_paths: ["客户画像/高净值客户"],
  tag_boost_factor: 1.2,
  metadata: {
    case_name: "案例A",
    stage: "需求分析",
    compliance_risks: ["收益承诺风险"]
  },
  citations: [
    {
      filename: "第2节.track-0.txt",
      source_type: "txt",
      source_path: "data/案例A/第2节.track-0.txt",
      display_location: "L1",
      display_excerpt: "客户担心预算的原文",
      locator_confidence: "validated_span",
      can_reveal: true
    },
    {
      filename: "第3节.track-0.txt",
      source_type: "txt",
      source_path: "data/案例A/第3节.track-0.txt",
      display_location: "L9",
      display_excerpt: "第二条引用原文",
      locator_confidence: "approximate",
      can_reveal: true
    }
  ]
};

test("明细不渲染任何标签，仍保留标题和重排分数", () => {
  render(<EvidenceDetail evidence={evidence} index={0} />);

  expect(screen.getByText("引用1：案例A · 需求分析")).toBeInTheDocument();
  expect(screen.getByText("重排 0.91")).toBeInTheDocument();
  expect(screen.queryByLabelText("引用1命中标签")).not.toBeInTheDocument();
  expect(screen.queryByText("客户画像/高净值客户")).not.toBeInTheDocument();
  expect(screen.queryByText("标签提权 ×1.2")).not.toBeInTheDocument();
  expect(
    screen.queryByText("合规注意 · 收益承诺风险")
  ).not.toBeInTheDocument();
});

test("元信息缺失时使用未命名知识作为标题", () => {
  render(
    <EvidenceDetail
      evidence={{ ...evidence, metadata: undefined }}
      index={1}
    />
  );

  expect(screen.getByText("引用2：未命名知识")).toBeInTheDocument();
});

test("引用较多时默认只显示前 4 条，可展开与收起", async () => {
  const user = userEvent.setup();
  const many: RetrievalEvidence = {
    ...evidence,
    citations: Array.from({ length: 7 }, (_, i) => ({
      filename: `片段${i}.txt`,
      source_type: "txt",
      source_path: `路径/${i}`,
      display_location: `L${i + 1}`,
      display_excerpt: `摘录${i}`,
      anchor_id: `anchor-${i}`,
      can_reveal: false
    }))
  };
  render(<EvidenceDetail evidence={many} index={0} />);

  // 默认只渲染前 4 条引用按钮。
  expect(screen.getByRole("button", { name: /片段0\.txt/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /片段3\.txt/ })).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /片段4\.txt/ })
  ).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /展开其余 3 条/ }));
  expect(screen.getByRole("button", { name: /片段6\.txt/ })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /收起来源引用/ }));
  expect(
    screen.queryByRole("button", { name: /片段4\.txt/ })
  ).not.toBeInTheDocument();
});

test("引用不超过阈值时不显示展开按钮", () => {
  render(<EvidenceDetail evidence={evidence} index={0} />);

  expect(
    screen.queryByRole("button", { name: /展开其余/ })
  ).not.toBeInTheDocument();
});

test("默认展示第一条来源摘录，点击其它引用切换", async () => {
  const user = userEvent.setup();
  render(<EvidenceDetail evidence={evidence} index={0} />);

  expect(screen.getByText("data/案例A/第2节.track-0.txt")).toBeInTheDocument();
  expect(screen.getByText("客户担心预算的原文")).toBeInTheDocument();

  await user.click(
    screen.getByRole("button", { name: "第3节.track-0.txt · L9 · 近似定位" })
  );

  expect(screen.getByText("data/案例A/第3节.track-0.txt")).toBeInTheDocument();
  expect(screen.getByText("第二条引用原文")).toBeInTheDocument();
});

test("在 Finder 中显示文件调用 reveal 接口", async () => {
  const user = userEvent.setup();
  const { requests } = installFetchStub();
  render(<EvidenceDetail evidence={evidence} index={0} />);

  await user.click(screen.getByRole("button", { name: "在 Finder 中显示文件" }));

  expect(await screen.findByText("已在 Finder 中显示文件。")).toBeInTheDocument();
  expect(requests).toContainEqual(
    expect.objectContaining({
      url: "/api/source/reveal",
      body: { source_path: "data/案例A/第2节.track-0.txt" }
    })
  );
});

function FeedbackHarness({
  onSubmit = vi.fn().mockResolvedValue(undefined),
  initialFeedback
}: {
  onSubmit?: (decision: EvidenceFeedbackDecision) => Promise<void>;
  initialFeedback?: EvidenceFeedback;
}) {
  const [feedback, setFeedback] = useState<EvidenceFeedback | undefined>(
    initialFeedback
  );

  async function handleSubmit(decision: EvidenceFeedbackDecision) {
    await onSubmit(decision);
    setFeedback({
      ...decision,
      chunk_id: evidence.chunk_id,
      label: "案例A · 需求分析",
      text_preview: evidence.text_preview ?? evidence.text ?? ""
    });
  }

  return (
    <EvidenceDetail
      evidence={evidence}
      index={0}
      feedback={feedback}
      onSubmitFeedback={handleSubmit}
    />
  );
}

test("初始只显示召回准确性维度且使用单选按钮", () => {
  render(<FeedbackHarness />);

  expect(
    screen.getByRole("group", { name: "召回是否准确？" })
  ).toBeInTheDocument();
  expect(screen.getByLabelText("引用1召回准确")).toHaveAttribute(
    "type",
    "radio"
  );
  expect(screen.getByLabelText("引用1召回不准确")).toHaveAttribute(
    "type",
    "radio"
  );
  expect(
    screen.queryByRole("group", { name: "回答是否正确参考该引用？" })
  ).not.toBeInTheDocument();
});

test("选择召回准确后显示回答参考维度且不提交", async () => {
  const user = userEvent.setup();
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<FeedbackHarness onSubmit={onSubmit} />);

  await user.click(screen.getByLabelText("引用1召回准确"));

  expect(screen.getByLabelText("引用1召回准确")).toBeChecked();
  expect(
    screen.getByRole("group", { name: "回答是否正确参考该引用？" })
  ).toBeInTheDocument();
  expect(screen.getByLabelText("引用1参考正确")).not.toBeChecked();
  expect(screen.getByLabelText("引用1参考不正确")).not.toBeChecked();
  expect(onSubmit).not.toHaveBeenCalled();
});

test("召回不准确要求填写原因并提交 trimmed payload", async () => {
  const user = userEvent.setup();
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<FeedbackHarness onSubmit={onSubmit} />);

  await user.click(screen.getByLabelText("引用1召回不准确"));

  const reason = screen.getByLabelText("召回不准确原因");
  expect(reason).toHaveAttribute(
    "placeholder",
    "例如：该引用与客户问题无关、客户或案例不匹配，未能回答当前异议。"
  );
  expect(screen.getByRole("button", { name: "保存反馈" })).toBeDisabled();

  await user.type(reason, "  该引用与客户问题无关。  ");
  await user.click(screen.getByRole("button", { name: "保存反馈" }));

  expect(onSubmit).toHaveBeenCalledWith({
    retrieval_judgement: "inaccurate",
    answer_usage_judgement: "not_applicable",
    reason: "该引用与客户问题无关。"
  });
  expect(await screen.findByText("已记录引用反馈。")).toBeInTheDocument();
});

test("参考不正确要求填写原因并提交 trimmed payload", async () => {
  const user = userEvent.setup();
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<FeedbackHarness onSubmit={onSubmit} />);

  await user.click(screen.getByLabelText("引用1召回准确"));
  await user.click(screen.getByLabelText("引用1参考不正确"));

  const reason = screen.getByLabelText("参考不正确原因");
  expect(reason).toHaveAttribute(
    "placeholder",
    "例如：回答曲解了引用原意、超出证据范围，或遗漏了关键限制。"
  );
  expect(screen.getByRole("button", { name: "保存反馈" })).toBeDisabled();

  await user.type(reason, "  回答超出了证据范围。  ");
  await user.click(screen.getByRole("button", { name: "保存反馈" }));

  expect(onSubmit).toHaveBeenCalledWith({
    retrieval_judgement: "accurate",
    answer_usage_judgement: "incorrect",
    reason: "回答超出了证据范围。"
  });
});

test("参考正确时立即提交并在保存后锁定所有单选按钮", async () => {
  const user = userEvent.setup();
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<FeedbackHarness onSubmit={onSubmit} />);

  await user.click(screen.getByLabelText("引用1召回准确"));
  await user.click(screen.getByLabelText("引用1参考正确"));

  expect(onSubmit).toHaveBeenCalledWith({
    retrieval_judgement: "accurate",
    answer_usage_judgement: "correct"
  });
  expect(await screen.findByText("已记录引用反馈。")).toBeInTheDocument();
  for (const radio of screen.getAllByRole("radio")) {
    expect(radio).toBeDisabled();
  }
});

test("参考正确保存中与 feedback 回填后都会锁定所有单选按钮", async () => {
  const user = userEvent.setup();
  let resolveSubmit: (() => void) | undefined;
  const onSubmit = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        resolveSubmit = resolve;
      })
  );
  render(<FeedbackHarness onSubmit={onSubmit} />);

  await user.click(screen.getByLabelText("引用1召回准确"));
  await user.click(screen.getByLabelText("引用1参考正确"));

  const savingRadios = screen.getAllByRole("radio");
  expect(savingRadios).toHaveLength(4);
  for (const radio of savingRadios) {
    expect(radio).toBeDisabled();
  }

  expect(resolveSubmit).toBeTypeOf("function");
  await act(async () => {
    resolveSubmit?.();
  });

  expect(screen.getByText("已记录引用反馈。")).toBeInTheDocument();
  const savedRadios = screen.getAllByRole("radio");
  expect(savedRadios).toHaveLength(4);
  for (const radio of savedRadios) {
    expect(radio).toBeDisabled();
  }
});

test("切换召回维度会清空不兼容的回答选择与原因", async () => {
  const user = userEvent.setup();
  render(<FeedbackHarness />);

  await user.click(screen.getByLabelText("引用1召回准确"));
  await user.click(screen.getByLabelText("引用1参考不正确"));
  await user.type(screen.getByLabelText("参考不正确原因"), "填了一半");
  await user.click(screen.getByLabelText("引用1召回不准确"));

  expect(screen.queryByLabelText("引用1参考不正确")).not.toBeInTheDocument();
  expect(screen.getByLabelText("召回不准确原因")).toHaveValue("");

  await user.click(screen.getByLabelText("引用1召回准确"));
  expect(screen.getByLabelText("引用1参考不正确")).not.toBeChecked();
  expect(screen.queryByLabelText("召回不准确原因")).not.toBeInTheDocument();
});

test("取消召回负向会清空两个维度与原因", async () => {
  const user = userEvent.setup();
  const onSubmit = vi.fn();
  render(<FeedbackHarness onSubmit={onSubmit} />);

  await user.click(screen.getByLabelText("引用1召回不准确"));
  await user.type(screen.getByLabelText("召回不准确原因"), "填了一半");
  await user.click(screen.getByRole("button", { name: "取消" }));

  expect(screen.getByLabelText("引用1召回准确")).not.toBeChecked();
  expect(screen.getByLabelText("引用1召回不准确")).not.toBeChecked();
  expect(screen.queryByLabelText("召回不准确原因")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("group", { name: "回答是否正确参考该引用？" })
  ).not.toBeInTheDocument();
  expect(onSubmit).not.toHaveBeenCalled();
});

test("取消回答负向会保留召回准确并清空回答与原因", async () => {
  const user = userEvent.setup();
  render(<FeedbackHarness />);

  await user.click(screen.getByLabelText("引用1召回准确"));
  await user.click(screen.getByLabelText("引用1参考不正确"));
  await user.type(screen.getByLabelText("参考不正确原因"), "填了一半");
  await user.click(screen.getByRole("button", { name: "取消" }));

  expect(screen.getByLabelText("引用1召回准确")).toBeChecked();
  expect(screen.getByLabelText("引用1参考正确")).not.toBeChecked();
  expect(screen.getByLabelText("引用1参考不正确")).not.toBeChecked();
  expect(screen.queryByLabelText("参考不正确原因")).not.toBeInTheDocument();
});

test("正向保存失败会清空回答选择并允许重新点击提交", async () => {
  const user = userEvent.setup();
  const onSubmit = vi
    .fn()
    .mockRejectedValueOnce(new Error("保存失败"))
    .mockResolvedValueOnce(undefined);
  render(<FeedbackHarness onSubmit={onSubmit} />);

  await user.click(screen.getByLabelText("引用1召回准确"));
  await user.click(screen.getByLabelText("引用1参考正确"));

  expect(await screen.findByText("保存失败")).toBeInTheDocument();
  expect(screen.getByLabelText("引用1参考正确")).not.toBeChecked();
  expect(screen.getByLabelText("引用1参考正确")).not.toBeDisabled();

  await user.click(screen.getByLabelText("引用1参考正确"));
  expect(onSubmit).toHaveBeenCalledTimes(2);
  expect(await screen.findByText("已记录引用反馈。")).toBeInTheDocument();
});

test("保存失败后修改召回维度会清除错误", async () => {
  const user = userEvent.setup();
  const onSubmit = vi.fn().mockRejectedValue(new Error("保存失败"));
  render(<FeedbackHarness onSubmit={onSubmit} />);

  await user.click(screen.getByLabelText("引用1召回准确"));
  await user.click(screen.getByLabelText("引用1参考正确"));
  expect(await screen.findByText("保存失败")).toBeInTheDocument();

  await user.click(screen.getByLabelText("引用1召回不准确"));

  expect(screen.queryByText("保存失败")).not.toBeInTheDocument();
  expect(screen.getByLabelText("召回不准确原因")).toBeInTheDocument();
});

test("成功消息尚未由 feedback 锁定时修改召回维度会清除消息", async () => {
  const user = userEvent.setup();
  const onSubmitFeedback = vi.fn().mockResolvedValue(undefined);
  render(
    <EvidenceDetail
      evidence={evidence}
      index={0}
      onSubmitFeedback={onSubmitFeedback}
    />
  );

  await user.click(screen.getByLabelText("引用1召回准确"));
  await user.click(screen.getByLabelText("引用1参考正确"));
  expect(await screen.findByText("已记录引用反馈。")).toBeInTheDocument();

  await user.click(screen.getByLabelText("引用1召回不准确"));

  expect(screen.queryByText("已记录引用反馈。")).not.toBeInTheDocument();
  expect(screen.getByLabelText("召回不准确原因")).toBeInTheDocument();
});

test("负向保存失败会保留原因表单并允许重试", async () => {
  const user = userEvent.setup();
  const onSubmit = vi
    .fn()
    .mockRejectedValueOnce(new Error("保存失败"))
    .mockResolvedValueOnce(undefined);
  render(<FeedbackHarness onSubmit={onSubmit} />);

  await user.click(screen.getByLabelText("引用1召回不准确"));
  await user.type(screen.getByLabelText("召回不准确原因"), "证据不相关");
  await user.click(screen.getByRole("button", { name: "保存反馈" }));

  expect(await screen.findByText("保存失败")).toBeInTheDocument();
  expect(screen.getByLabelText("召回不准确原因")).toHaveValue("证据不相关");
  expect(screen.getByRole("button", { name: "保存反馈" })).not.toBeDisabled();

  await user.click(screen.getByRole("button", { name: "保存反馈" }));
  expect(onSubmit).toHaveBeenCalledTimes(2);
  expect(await screen.findByText("已记录引用反馈。")).toBeInTheDocument();
});

test("已保存反馈会初始化选择并锁定控件", () => {
  render(
    <FeedbackHarness
      initialFeedback={{
        chunk_id: "c1",
        retrieval_judgement: "accurate",
        answer_usage_judgement: "incorrect",
        reason: "回答遗漏了限制",
        label: "案例A · 需求分析",
        text_preview: "客户担心预算"
      }}
    />
  );

  expect(screen.getByLabelText("引用1召回准确")).toBeChecked();
  expect(screen.getByLabelText("引用1参考不正确")).toBeChecked();
  expect(screen.queryByLabelText("参考不正确原因")).not.toBeInTheDocument();
  for (const radio of screen.getAllByRole("radio")) {
    expect(radio).toBeDisabled();
  }
});

test("切换引用后再返回会恢复对应的已保存选择", () => {
  const savedFeedback: EvidenceFeedback = {
    chunk_id: "c1",
    retrieval_judgement: "accurate",
    answer_usage_judgement: "correct",
    label: "案例A · 需求分析",
    text_preview: "客户担心预算"
  };
  const onSubmitFeedback = vi.fn().mockResolvedValue(undefined);
  const { rerender } = render(
    <EvidenceDetail
      evidence={evidence}
      index={0}
      feedback={savedFeedback}
      onSubmitFeedback={onSubmitFeedback}
    />
  );

  rerender(
    <EvidenceDetail
      evidence={{ ...evidence, chunk_id: "c2" }}
      index={1}
      onSubmitFeedback={onSubmitFeedback}
    />
  );
  expect(screen.getByLabelText("引用2召回准确")).not.toBeChecked();

  rerender(
    <EvidenceDetail
      evidence={evidence}
      index={0}
      feedback={savedFeedback}
      onSubmitFeedback={onSubmitFeedback}
    />
  );
  expect(screen.getByLabelText("引用1召回准确")).toBeChecked();
  expect(screen.getByLabelText("引用1参考正确")).toBeChecked();
});

test("没有提交回调时不渲染反馈区", () => {
  render(<EvidenceDetail evidence={evidence} index={0} />);

  expect(screen.queryByText("召回是否准确？")).not.toBeInTheDocument();
});

test("正文只按固定顺序展示三个异议字段", () => {
  const structured: RetrievalEvidence = {
    ...evidence,
    text: [
      "案例：案例A",
      "推荐回应：先承接预算，再对齐保障缺口",
      "异议诊断：预算顾虑背后是保障优先级不清晰",
      "客户异议：客户说每年不能超过80万",
      "关联话术：script_009",
      "来源原文：",
      "- 第2节.track-0.txt：客户说每年保费预算不能超过80万"
    ].join("\n")
  };
  render(<EvidenceDetail evidence={structured} index={0} />);

  const detail = screen.getByRole("article", { name: "引用1明细" });
  const labels = Array.from(
    detail.querySelectorAll(".evidence-field-label")
  ).map((node) => node.textContent);
  expect(labels).toEqual(["客户异议", "异议诊断", "推荐回应"]);
  expect(screen.queryByText("案例")).not.toBeInTheDocument();
  expect(screen.queryByText("关联话术")).not.toBeInTheDocument();
  expect(screen.queryByText("来源原文")).not.toBeInTheDocument();
});

test("正文没有异议字段时显示空状态且不回退原始全文", () => {
  render(
    <EvidenceDetail
      evidence={{ ...evidence, text: "客户担心预算，可以先承接预算。" }}
      index={0}
    />
  );

  expect(screen.getByText("暂无异议处理内容。")).toBeInTheDocument();
  expect(
    screen.queryByText("客户担心预算，可以先承接预算。")
  ).not.toBeInTheDocument();
});
