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

test("明细渲染正文、标签、合规与元信息", () => {
  render(<EvidenceDetail evidence={evidence} index={0} cited />);

  expect(screen.getByText("证据 1 · 异议处理")).toBeInTheDocument();
  expect(screen.getByText("答案引用")).toBeInTheDocument();
  expect(screen.getByText("重排 0.91")).toBeInTheDocument();
  expect(screen.getByText("案例A · 需求分析")).toBeInTheDocument();
  expect(
    screen.getByText("客户担心预算，可以先承接预算，再对齐保障缺口。")
  ).toBeInTheDocument();
  expect(screen.getByText("客户画像/高净值客户")).toBeInTheDocument();
  expect(screen.getByText("标签提权 ×1.2")).toBeInTheDocument();
  expect(screen.getByText("合规注意 · 收益承诺风险")).toBeInTheDocument();
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
  render(<EvidenceDetail evidence={many} index={0} cited={false} />);

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
  render(<EvidenceDetail evidence={evidence} index={0} cited={false} />);

  expect(
    screen.queryByRole("button", { name: /展开其余/ })
  ).not.toBeInTheDocument();
});

test("默认展示第一条来源摘录，点击其它引用切换", async () => {
  const user = userEvent.setup();
  render(<EvidenceDetail evidence={evidence} index={0} cited={false} />);

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
  render(<EvidenceDetail evidence={evidence} index={0} cited={false} />);

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
      cited={false}
      feedbackJudgement="should_use"
      onToggleFeedback={() => {}}
    />
  );

  expect(screen.getByLabelText("证据 1 应该用")).toBeChecked();
  expect(screen.getByLabelText("证据 1 不该用")).toBeInTheDocument();
  expect(screen.queryByLabelText("证据 1 排序太低")).not.toBeInTheDocument();
});

test("点击应该用立即选中并落地正向 bad case", async () => {
  const user = userEvent.setup();
  const onSubmitUseful = vi.fn().mockResolvedValue(undefined);
  render(<FeedbackHarness onSubmitUseful={onSubmitUseful} />);

  await user.click(screen.getByLabelText("证据 1 应该用"));

  expect(screen.getByLabelText("证据 1 应该用")).toBeChecked();
  expect(onSubmitUseful).toHaveBeenCalledTimes(1);
  expect(await screen.findByText("已记录可用反馈。")).toBeInTheDocument();
});

// 受控 harness：复刻 BadCasePanel 的 toggle 语义（同判定再点取消、不同判定覆盖），
// 用于断言应该用/不该用的单选互斥与选中态。
function FeedbackHarness({
  onSubmitUseful,
  onSubmitNotUseful
}: {
  onSubmitUseful?: () => Promise<void>;
  onSubmitNotUseful?: (reason: string) => Promise<void>;
}) {
  const [judgement, setJudgement] = useState<
    EvidenceFeedbackJudgement | undefined
  >(undefined);
  return (
    <EvidenceDetail
      evidence={evidence}
      index={0}
      cited={false}
      feedbackJudgement={judgement}
      onToggleFeedback={(next) =>
        setJudgement((current) => (current === next ? undefined : next))
      }
      onSubmitUseful={onSubmitUseful ?? vi.fn().mockResolvedValue(undefined)}
      onSubmitNotUseful={onSubmitNotUseful ?? vi.fn().mockResolvedValue(undefined)}
    />
  );
}

test("点击不该用立即选中并展开理由输入，保存后提交理由", async () => {
  const user = userEvent.setup();
  const onSubmitNotUseful = vi.fn().mockResolvedValue(undefined);
  render(<FeedbackHarness onSubmitNotUseful={onSubmitNotUseful} />);

  await user.click(screen.getByLabelText("证据 1 不该用"));
  // 点击后立即呈选中态，不等保存。
  expect(screen.getByLabelText("证据 1 不该用")).toBeChecked();
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
  expect(screen.getByLabelText("证据 1 不该用")).toBeChecked();
});

test("应该用与不该用单选互斥", async () => {
  const user = userEvent.setup();
  render(<FeedbackHarness />);

  await user.click(screen.getByLabelText("证据 1 应该用"));
  expect(screen.getByLabelText("证据 1 应该用")).toBeChecked();

  await user.click(screen.getByLabelText("证据 1 不该用"));
  expect(screen.getByLabelText("证据 1 不该用")).toBeChecked();
  expect(screen.getByLabelText("证据 1 应该用")).not.toBeChecked();

  // 反向切回应该用，同时收起理由框。
  await user.click(screen.getByLabelText("证据 1 应该用"));
  expect(screen.getByLabelText("证据 1 应该用")).toBeChecked();
  expect(screen.getByLabelText("证据 1 不该用")).not.toBeChecked();
  expect(screen.queryByLabelText("不可用理由")).not.toBeInTheDocument();
});

test("取消理由输入恢复之前的判定且不提交", async () => {
  const user = userEvent.setup();
  const onSubmitNotUseful = vi.fn();
  render(<FeedbackHarness onSubmitNotUseful={onSubmitNotUseful} />);

  await user.click(screen.getByLabelText("证据 1 应该用"));
  await user.click(screen.getByLabelText("证据 1 不该用"));
  await user.type(screen.getByLabelText("不可用理由"), "填了一半");
  await user.click(screen.getByRole("button", { name: "取消" }));

  expect(screen.queryByLabelText("不可用理由")).not.toBeInTheDocument();
  expect(onSubmitNotUseful).not.toHaveBeenCalled();
  // 恢复打开理由框之前的“应该用”。
  expect(screen.getByLabelText("证据 1 应该用")).toBeChecked();
  expect(screen.getByLabelText("证据 1 不该用")).not.toBeChecked();
});

test("原本未打标时取消理由输入清空判定", async () => {
  const user = userEvent.setup();
  render(<FeedbackHarness />);

  await user.click(screen.getByLabelText("证据 1 不该用"));
  await user.click(screen.getByRole("button", { name: "取消" }));

  expect(screen.getByLabelText("证据 1 不该用")).not.toBeChecked();
  expect(screen.getByLabelText("证据 1 应该用")).not.toBeChecked();
});

test("已是不该用时再点取消本地判定并收起理由框", async () => {
  const user = userEvent.setup();
  render(<FeedbackHarness />);

  await user.click(screen.getByLabelText("证据 1 不该用"));
  expect(screen.getByLabelText("不可用理由")).toBeInTheDocument();

  await user.click(screen.getByLabelText("证据 1 不该用"));

  expect(screen.getByLabelText("证据 1 不该用")).not.toBeChecked();
  expect(screen.queryByLabelText("不可用理由")).not.toBeInTheDocument();
});

test("没有打标回调时不渲染判定操作", () => {
  render(<EvidenceDetail evidence={evidence} index={0} cited={false} />);

  expect(screen.queryByLabelText("证据 1 应该用")).not.toBeInTheDocument();
});

test("结构化正文按字段着色区分 AI 归纳与案例原文", () => {
  const structured: RetrievalEvidence = {
    ...evidence,
    text: [
      "案例：案例A",
      "知识类型：异议处理",
      "客户异议：客户说每年不能超过80万",
      "推荐回应：先承接预算，再对齐保障缺口",
      "来源原文：",
      "- 第2节.track-0.txt：客户说每年保费预算不能超过80万"
    ].join("\n")
  };
  render(<EvidenceDetail evidence={structured} index={0} cited={false} />);

  // 图例说明两种来源。
  expect(screen.getByText("AI 归纳")).toBeInTheDocument();
  expect(screen.getByText("案例原文")).toBeInTheDocument();
  // 模型归纳字段带 generated 标签，原文块带 source 标签。
  const objectionLabel = screen.getByText("客户异议");
  expect(objectionLabel.className).toBe("evidence-field-label");
  expect(
    screen.getByText("客户说每年不能超过80万")
  ).toBeInTheDocument();
  const sourceLabel = screen.getByText("来源原文");
  expect(sourceLabel.className).toBe("evidence-field-label source");
  // 来源原文默认折叠，原文不直接渲染。
  expect(
    screen.queryByText("第2节.track-0.txt：客户说每年保费预算不能超过80万")
  ).not.toBeInTheDocument();
});

test("来源原文默认折叠，点击标题展开、再点击折叠", async () => {
  const user = userEvent.setup();
  const structured: RetrievalEvidence = {
    ...evidence,
    text: [
      "推荐回应：先承接预算",
      "来源原文：",
      "- 第2节.track-0.txt：客户说每年保费预算不能超过80万",
      "- 第3节.track-0.txt：先看家庭责任额度"
    ].join("\n")
  };
  render(<EvidenceDetail evidence={structured} index={0} cited={false} />);

  const toggle = screen.getByRole("button", { name: /来源原文/ });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(
    screen.queryByText("第2节.track-0.txt：客户说每年保费预算不能超过80万")
  ).not.toBeInTheDocument();

  await user.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(
    screen.getByText("第2节.track-0.txt：客户说每年保费预算不能超过80万")
  ).toBeInTheDocument();
  expect(
    screen.getByText("第3节.track-0.txt：先看家庭责任额度")
  ).toBeInTheDocument();

  await user.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(
    screen.queryByText("第2节.track-0.txt：客户说每年保费预算不能超过80万")
  ).not.toBeInTheDocument();
});

test("关联话术保持 ID 展示，点击后内联展开完整话术", async () => {
  const user = userEvent.setup();
  const structured: RetrievalEvidence = {
    ...evidence,
    text: [
      "案例：案例A",
      "知识类型：异议处理",
      "客户异议：保险收益不如银行/产品有坏处吗（精明型客户）",
      "推荐回应：坦诚告知流动性限制",
      "关联话术：script_009"
    ].join("\n"),
    metadata: {
      case_name: "案例A",
      related_script_ids: ["script_009"],
      related_script_details: [
        {
          script_id: "script_009",
          stage: "产品讲解/异议处理",
          scenario: "精明型客户质疑保险收益不如银行理财或有坏处",
          customer_trigger: "客户直接询问收益率、流动性限制或产品缺陷",
          goal: "坦诚披露弊端以赢得信任",
          source_quote:
            "短期没有，长期任何一家金融工具不能和保险相抗衡。",
          coach_wording:
            "王总，坦白讲，保险短期内确实没有高收益，本金也有锁定期。",
          strategy_names: ["性格四象限差异化沟通策略"],
          follow_up_questions: ["您是否更看重长期确定的现金流安排？"],
          compliance_notes: ["严禁夸大分红或万能账户结算利率"]
        }
      ]
    }
  };
  render(<EvidenceDetail evidence={structured} index={0} cited={false} />);

  const scriptButton = screen.getByRole("button", { name: "script_009" });
  expect(scriptButton).toHaveAttribute("aria-expanded", "false");
  expect(
    screen.queryByText("精明型客户质疑保险收益不如银行理财或有坏处")
  ).not.toBeInTheDocument();

  await user.click(scriptButton);

  expect(scriptButton).toHaveAttribute("aria-expanded", "true");
  expect(
    screen.getByText("精明型客户质疑保险收益不如银行理财或有坏处")
  ).toBeInTheDocument();
  expect(
    screen.getByText("王总，坦白讲，保险短期内确实没有高收益，本金也有锁定期。")
  ).toBeInTheDocument();
  expect(
    screen.getByText("严禁夸大分红或万能账户结算利率")
  ).toBeInTheDocument();
});

test("AI 归纳的 bullet 块默认展开", () => {
  const structured: RetrievalEvidence = {
    ...evidence,
    text: ["关键动作：", "- 先做保障缺口分析", "- 再谈缴费期"].join("\n")
  };
  render(<EvidenceDetail evidence={structured} index={0} cited={false} />);

  // 生成类要点是核心内容，直接展开、无折叠开关。
  expect(screen.getByText("先做保障缺口分析")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /关键动作/ })
  ).not.toBeInTheDocument();
});

test("非结构化正文回退为纯文本展示", () => {
  render(<EvidenceDetail evidence={evidence} index={0} cited={false} />);

  expect(
    screen.getByText("客户担心预算，可以先承接预算，再对齐保障缺口。")
  ).toBeInTheDocument();
  expect(screen.queryByText("AI 归纳")).not.toBeInTheDocument();
});
