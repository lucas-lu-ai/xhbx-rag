import { render, screen } from "@testing-library/react";

import { EvidenceList } from "./EvidenceList";
import type { RetrievalEvidence } from "../types";

function renderEvidences(evidences: RetrievalEvidence[]) {
  render(
    <EvidenceList
      evidences={evidences}
      keyPrefix="turn-1"
      citedIndexes={new Set()}
      feedback={{}}
      feedbackKeyOf={(index) => `feedback-${index}`}
      onToggleFeedback={() => {}}
    />
  );
}

test("命中查询标签的证据显示标签 chip 与提权倍数", () => {
  renderEvidences([
    {
      chunk_id: "c1",
      chunk_type: "script",
      text: "高净值客户传承话术",
      rerank_score: 0.9,
      matched_tag_paths: ["客户画像/高净值客户", "客户需求/财富传承"],
      tag_boost_factor: 1.2,
      citations: []
    },
    {
      chunk_id: "c2",
      chunk_type: "script",
      text: "普通开场话术",
      citations: []
    }
  ]);

  expect(screen.getByText("客户画像/高净值客户")).toBeInTheDocument();
  expect(screen.getByText("客户需求/财富传承")).toBeInTheDocument();
  expect(screen.getByText("标签提权 ×1.2")).toBeInTheDocument();
  expect(screen.getAllByText(/标签提权/)).toHaveLength(1);
});

test("没有命中标签时不渲染标签区域", () => {
  renderEvidences([
    {
      chunk_id: "c1",
      chunk_type: "script",
      text: "普通话术",
      matched_tag_paths: [],
      tag_boost_factor: 1.0,
      citations: []
    }
  ]);

  expect(screen.queryByText(/标签提权/)).not.toBeInTheDocument();
});

test("带合规风险标签的证据显示合规注意徽标", () => {
  renderEvidences([
    {
      chunk_id: "c1",
      chunk_type: "script",
      text: "涉及收益表述的话术",
      metadata: { compliance_risks: ["收益承诺风险", "适当性风险"] },
      citations: []
    },
    {
      chunk_id: "c2",
      chunk_type: "script",
      text: "普通话术",
      citations: []
    }
  ]);

  expect(
    screen.getByText("合规注意 · 收益承诺风险、适当性风险")
  ).toBeInTheDocument();
  expect(screen.getAllByText(/合规注意/)).toHaveLength(1);
});
