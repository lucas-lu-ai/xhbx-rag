import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { EvidenceDetail } from "./EvidenceDetail";
import { installFetchStub, runRegisteredCleanups } from "../test-utils";
import type {
  EvidenceFeedbackJudgement,
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

test("打标只提供应该用与不该用两个选项", () => {
  render(
    <EvidenceDetail
      evidence={evidence}
      index={0}
      feedbackJudgement="should_use"
      onToggleFeedback={() => {}}
    />
  );

  expect(screen.getByLabelText("引用1打标")).toBeInTheDocument();
  expect(screen.getByLabelText("引用1应该用")).toBeChecked();
  expect(screen.getByLabelText("引用1不该用")).toBeInTheDocument();
  expect(screen.queryByLabelText("引用1排序太低")).not.toBeInTheDocument();
});

test("点击应该用立即选中并落地正向 bad case", async () => {
  const user = userEvent.setup();
  const onSubmitUseful = vi.fn().mockResolvedValue(undefined);
  render(<FeedbackHarness onSubmitUseful={onSubmitUseful} />);

  await user.click(screen.getByLabelText("引用1应该用"));

  expect(screen.getByLabelText("引用1应该用")).toBeChecked();
  expect(onSubmitUseful).toHaveBeenCalledTimes(1);
  expect(await screen.findByText("已记录可用反馈。")).toBeInTheDocument();
  expect(screen.getByLabelText("引用1应该用")).toBeDisabled();
  expect(screen.getByLabelText("引用1不该用")).toBeDisabled();

  await user.click(screen.getByLabelText("引用1不该用"));

  expect(onSubmitUseful).toHaveBeenCalledTimes(1);
  expect(screen.queryByLabelText("不可用理由")).not.toBeInTheDocument();
});

// 受控 harness：复刻 BadCasePanel 的 toggle 语义（同判定再点取消、不同判定覆盖），
// 用于断言应该用/不该用的单选互斥与选中态。
function FeedbackHarness({
  onSubmitUseful,
  onSubmitNotUseful
}: {
  onSubmitUseful?: (() => Promise<void>) | null;
  onSubmitNotUseful?: (reason: string) => Promise<void>;
}) {
  const [judgement, setJudgement] = useState<
    EvidenceFeedbackJudgement | undefined
  >(undefined);
  return (
    <EvidenceDetail
      evidence={evidence}
      index={0}
      feedbackJudgement={judgement}
      onToggleFeedback={(next) =>
        setJudgement((current) => (current === next ? undefined : next))
      }
      onSubmitUseful={
        onSubmitUseful === null
          ? undefined
          : (onSubmitUseful ?? vi.fn().mockResolvedValue(undefined))
      }
      onSubmitNotUseful={onSubmitNotUseful ?? vi.fn().mockResolvedValue(undefined)}
    />
  );
}

test("点击不该用立即选中并展开理由输入，保存后提交理由", async () => {
  const user = userEvent.setup();
  const onSubmitNotUseful = vi.fn().mockResolvedValue(undefined);
  render(<FeedbackHarness onSubmitNotUseful={onSubmitNotUseful} />);

  await user.click(screen.getByLabelText("引用1不该用"));
  // 点击后立即呈选中态，不等保存。
  expect(screen.getByLabelText("引用1不该用")).toBeChecked();
  expect(screen.getByLabelText("不可用理由")).toBeInTheDocument();
  // 理由为空时不能保存。
  expect(
    screen.getByRole("button", { name: "保存不可用反馈" })
  ).toBeDisabled();

  await user.type(
    screen.getByLabelText("不可用理由"),
    "该证据与客户问题无关。"
  );
  await user.click(screen.getByRole("button", { name: "保存不可用反馈" }));

  expect(onSubmitNotUseful).toHaveBeenCalledWith("该证据与客户问题无关。");
  expect(await screen.findByText("已记录不可用反馈。")).toBeInTheDocument();
  expect(screen.queryByLabelText("不可用理由")).not.toBeInTheDocument();
  expect(screen.getByLabelText("引用1不该用")).toBeChecked();
  expect(screen.getByLabelText("引用1应该用")).toBeDisabled();
  expect(screen.getByLabelText("引用1不该用")).toBeDisabled();
});

test("反馈保存失败后不锁定选项并允许重试", async () => {
  const user = userEvent.setup();
  const onSubmitUseful = vi
    .fn()
    .mockRejectedValueOnce(new Error("保存失败"))
    .mockResolvedValueOnce(undefined);
  render(<FeedbackHarness onSubmitUseful={onSubmitUseful} />);

  await user.click(screen.getByLabelText("引用1应该用"));

  expect(screen.getByLabelText("引用1应该用")).not.toBeDisabled();
  expect(screen.getByLabelText("引用1不该用")).not.toBeDisabled();

  await user.click(screen.getByLabelText("引用1应该用"));
  await user.click(screen.getByLabelText("引用1应该用"));

  expect(onSubmitUseful).toHaveBeenCalledTimes(2);
  expect(await screen.findByText("已记录可用反馈。")).toBeInTheDocument();
  expect(screen.getByLabelText("引用1应该用")).toBeDisabled();
  expect(screen.getByLabelText("引用1不该用")).toBeDisabled();
});

test("应该用与不该用单选互斥", async () => {
  const user = userEvent.setup();
  render(<FeedbackHarness onSubmitUseful={null} />);

  await user.click(screen.getByLabelText("引用1应该用"));
  expect(screen.getByLabelText("引用1应该用")).toBeChecked();

  await user.click(screen.getByLabelText("引用1不该用"));
  expect(screen.getByLabelText("引用1不该用")).toBeChecked();
  expect(screen.getByLabelText("引用1应该用")).not.toBeChecked();

  // 反向切回应该用，同时收起理由框。
  await user.click(screen.getByLabelText("引用1应该用"));
  expect(screen.getByLabelText("引用1应该用")).toBeChecked();
  expect(screen.getByLabelText("引用1不该用")).not.toBeChecked();
  expect(screen.queryByLabelText("不可用理由")).not.toBeInTheDocument();
});

test("取消理由输入恢复之前的判定且不提交", async () => {
  const user = userEvent.setup();
  const onSubmitNotUseful = vi.fn();
  render(
    <FeedbackHarness
      onSubmitUseful={null}
      onSubmitNotUseful={onSubmitNotUseful}
    />
  );

  await user.click(screen.getByLabelText("引用1应该用"));
  await user.click(screen.getByLabelText("引用1不该用"));
  await user.type(screen.getByLabelText("不可用理由"), "填了一半");
  await user.click(screen.getByRole("button", { name: "取消" }));

  expect(screen.queryByLabelText("不可用理由")).not.toBeInTheDocument();
  expect(onSubmitNotUseful).not.toHaveBeenCalled();
  // 恢复打开理由框之前的“应该用”。
  expect(screen.getByLabelText("引用1应该用")).toBeChecked();
  expect(screen.getByLabelText("引用1不该用")).not.toBeChecked();
});

test("原本未打标时取消理由输入清空判定", async () => {
  const user = userEvent.setup();
  render(<FeedbackHarness />);

  await user.click(screen.getByLabelText("引用1不该用"));
  await user.click(screen.getByRole("button", { name: "取消" }));

  expect(screen.getByLabelText("引用1不该用")).not.toBeChecked();
  expect(screen.getByLabelText("引用1应该用")).not.toBeChecked();
});

test("已是不该用时再点取消本地判定并收起理由框", async () => {
  const user = userEvent.setup();
  render(<FeedbackHarness />);

  await user.click(screen.getByLabelText("引用1不该用"));
  expect(screen.getByLabelText("不可用理由")).toBeInTheDocument();

  await user.click(screen.getByLabelText("引用1不该用"));

  expect(screen.getByLabelText("引用1不该用")).not.toBeChecked();
  expect(screen.queryByLabelText("不可用理由")).not.toBeInTheDocument();
});

test("没有打标回调时不渲染判定操作", () => {
  render(<EvidenceDetail evidence={evidence} index={0} />);

  expect(screen.queryByLabelText("引用1应该用")).not.toBeInTheDocument();
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
