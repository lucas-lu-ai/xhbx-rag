import { fireEvent, render, screen, within } from "@testing-library/react";

import { EvidenceList } from "./EvidenceList";
import type { RetrievalEvidence } from "../types";

const sampleEvidences: RetrievalEvidence[] = [
  {
    chunk_id: "c1",
    chunk_type: "objection_handling",
    text: "客户担心预算，可以先承接预算，再对齐保障缺口。",
    rerank_score: 0.91,
    metadata: { case_name: "案例A", stage: "需求分析" },
    citations: []
  },
  {
    chunk_id: "c2",
    chunk_type: "script",
    text: "缴费期调整话术。",
    citations: []
  }
];

function renderList(
  props: Partial<Parameters<typeof EvidenceList>[0]> = {}
) {
  render(
    <EvidenceList
      evidences={sampleEvidences}
      keyPrefix="turn-1"
      citedIndexes={new Set([2])}
      {...props}
    />
  );
}

function expandList() {
  fireEvent.click(screen.getByRole("button", { name: /知识引用/ }));
}

test("知识引用列表默认折叠，点击标题展开、再点击折叠", () => {
  renderList();

  const toggle = screen.getByRole("button", { name: /知识引用/ });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(
    screen.queryByRole("region", { name: "知识引用列表" })
  ).not.toBeInTheDocument();

  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(
    screen.getByRole("region", { name: "知识引用列表" })
  ).toBeInTheDocument();

  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(
    screen.queryByRole("region", { name: "知识引用列表" })
  ).not.toBeInTheDocument();
});

test("标题栏只显示知识引用条数，不显示答案引用文案", () => {
  renderList();

  expect(screen.getByText("知识引用")).toBeInTheDocument();
  expect(screen.getByText("1 条")).toBeInTheDocument();
  expect(screen.queryByText(/答案引用/)).not.toBeInTheDocument();
});

test("只显示实际引用知识，序号连续且不显示答案引用徽标", () => {
  renderList();
  expandList();

  const region = screen.getByRole("region", { name: "知识引用列表" });
  const rows = within(region).getAllByRole("button");
  expect(rows).toHaveLength(1);
  expect(within(rows[0]).getByText("1")).toBeInTheDocument();
  expect(within(rows[0]).getByText("未命名知识")).toBeInTheDocument();
  expect(within(rows[0]).getByText("销售话术")).toBeInTheDocument();
  expect(within(rows[0]).getByText("缴费期调整话术。")).toBeInTheDocument();
  expect(screen.queryByText("案例A · 需求分析")).not.toBeInTheDocument();
  expect(screen.queryByText("异议处理")).not.toBeInTheDocument();
  expect(screen.queryByText(/答案引用/)).not.toBeInTheDocument();
});

test("紧凑行保留知识名称、类型、重排分与单行预览", () => {
  renderList({ citedIndexes: new Set([1]) });
  expandList();

  const row = within(
    screen.getByRole("region", { name: "知识引用列表" })
  ).getByRole("button");
  expect(within(row).getByText("案例A · 需求分析")).toBeInTheDocument();
  expect(within(row).getByText("异议处理")).toBeInTheDocument();
  expect(within(row).getByText("0.91")).toBeInTheDocument();
  expect(
    within(row).getByText("客户担心预算，可以先承接预算，再对齐保障缺口。")
  ).toBeInTheDocument();
});

test("点击可见行回调原始 evidence key，选中行 aria-pressed", () => {
  const onSelectEvidence = vi.fn();
  renderList({
    selectedEvidenceKey: "turn-1:evidence-1",
    onSelectEvidence
  });
  expandList();

  const row = within(
    screen.getByRole("region", { name: "知识引用列表" })
  ).getByRole("button");
  expect(row).toHaveAttribute("aria-pressed", "true");

  fireEvent.click(row);
  expect(onSelectEvidence).toHaveBeenCalledWith("turn-1:evidence-1");
});

test("稀疏引用显示连续序号，点击仍回调原始 evidence key", () => {
  const onSelectEvidence = vi.fn();
  const evidences: RetrievalEvidence[] = [
    ...sampleEvidences,
    {
      chunk_id: "c3",
      chunk_type: "product_knowledge",
      text: "第三条产品知识。",
      citations: []
    }
  ];
  renderList({
    evidences,
    citedIndexes: new Set([1, 3]),
    onSelectEvidence
  });
  expandList();

  const rows = within(
    screen.getByRole("region", { name: "知识引用列表" })
  ).getAllByRole("button");
  expect(rows).toHaveLength(2);
  expect(within(rows[0]).getByText("1")).toBeInTheDocument();
  expect(within(rows[1]).getByText("2")).toBeInTheDocument();
  expect(within(rows[1]).getByText("第三条产品知识。")).toBeInTheDocument();

  fireEvent.click(rows[1]);
  expect(onSelectEvidence).toHaveBeenCalledWith("turn-1:evidence-2");
});

test("没有实际引用时不渲染知识引用区", () => {
  const { container } = render(
    <EvidenceList
      evidences={sampleEvidences}
      keyPrefix="turn-1"
      citedIndexes={new Set()}
    />
  );

  expect(container).toBeEmptyDOMElement();
  expect(screen.queryByText("知识引用")).not.toBeInTheDocument();
});
