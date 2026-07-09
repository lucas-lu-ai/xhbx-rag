import { fireEvent, render, screen } from "@testing-library/react";

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
      citedIndexes={new Set([1])}
      {...props}
    />
  );
}

function expandList() {
  fireEvent.click(screen.getByRole("button", { name: /检索证据/ }));
}

test("证据列表默认折叠，点击标题展开、再点击折叠", () => {
  renderList();

  const toggle = screen.getByRole("button", { name: /检索证据/ });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(
    screen.queryByRole("region", { name: "检索证据列表" })
  ).not.toBeInTheDocument();

  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("案例A · 需求分析")).toBeInTheDocument();

  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("案例A · 需求分析")).not.toBeInTheDocument();
});

test("标题栏显示证据总数与答案引用条数", () => {
  renderList();

  expect(screen.getByText("2 条 · 答案引用 1 条")).toBeInTheDocument();
});

test("紧凑行显示名称、类型、引用徽标、重排分与单行预览", () => {
  renderList();
  expandList();

  const rows = screen.getAllByRole("button", { name: /案例A|证据 2/ });
  expect(rows).toHaveLength(2);
  expect(screen.getByText("案例A · 需求分析")).toBeInTheDocument();
  expect(screen.getByText("异议处理")).toBeInTheDocument();
  expect(screen.getByText("答案引用")).toBeInTheDocument();
  expect(screen.getByText("0.91")).toBeInTheDocument();
  expect(
    screen.getByText("客户担心预算，可以先承接预算，再对齐保障缺口。")
  ).toBeInTheDocument();
  // 没有 metadata 的证据回退到“证据 N”，未知以外的类型正常中文化。
  expect(screen.getByText("证据 2")).toBeInTheDocument();
  expect(screen.getByText("销售话术")).toBeInTheDocument();
});

test("点击行回调选中 key，选中行 aria-pressed", () => {
  const onSelectEvidence = vi.fn();
  renderList({
    selectedEvidenceKey: "turn-1:evidence-1",
    onSelectEvidence
  });
  expandList();

  const [firstRow, secondRow] = screen
    .getAllByRole("button")
    .filter((button) => button.className.includes("evidence-row"));
  expect(firstRow).toHaveAttribute("aria-pressed", "false");
  expect(secondRow).toHaveAttribute("aria-pressed", "true");

  fireEvent.click(firstRow);
  expect(onSelectEvidence).toHaveBeenCalledWith("turn-1:evidence-0");
});
