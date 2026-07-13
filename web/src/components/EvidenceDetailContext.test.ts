import type { Citation, RetrievalEvidence } from "../types";
import * as evidenceDetailContext from "./EvidenceDetailContext";

const evidences: RetrievalEvidence[] = [
  { chunk_id: "evidence-1", text: "第一条证据" },
  { chunk_id: "evidence-2", text: "第二条证据" },
  { chunk_id: "evidence-3", text: "第三条证据" }
];

const citations: Citation[] = [
  {
    display_location: "证据 3",
    display_excerpt: "第三条证据",
    can_reveal: false,
    selected: true,
    evidence_index: 3
  },
  {
    display_location: "证据 2",
    display_excerpt: "第二条证据",
    can_reveal: false,
    selected: false,
    evidence_index: 2
  },
  {
    display_location: "证据 1",
    display_excerpt: "第一条证据",
    can_reveal: false,
    selected: true,
    evidence_index: 1
  }
];

test("引用视图按原始证据顺序保留索引并生成连续可见序号", () => {
  const entries = evidenceDetailContext.citedEvidenceEntries(
    evidences,
    evidenceDetailContext.citedEvidenceIndexes(citations)
  );

  expect(entries).toEqual([
    { evidence: evidences[0], evidenceIndex: 0, displayIndex: 0 },
    { evidence: evidences[2], evidenceIndex: 2, displayIndex: 1 }
  ]);
});

test("默认选中第一条实际引用的证据", () => {
  const nonLeadingCitations = [citations[0], citations[1]];

  expect(
    evidenceDetailContext.firstEvidenceKey(
      "turn-1",
      nonLeadingCitations,
      evidences
    )
  ).toBe("turn-1:evidence-2");
});

test("没有实际引用时不回退选中召回证据", () => {
  const unselectedCitations = citations.map((citation) => ({
    ...citation,
    selected: false
  }));

  expect(
    evidenceDetailContext.firstEvidenceKey(
      "turn-1",
      unselectedCitations,
      evidences
    )
  ).toBeNull();
  expect(
    evidenceDetailContext.firstEvidenceKey("turn-1", [], evidences)
  ).toBeNull();
});

test("重复引用同一证据时只生成一条引用视图", () => {
  const duplicateCitations: Citation[] = [
    citations[0],
    { ...citations[0] }
  ];

  expect(
    evidenceDetailContext.citedEvidenceEntries(
      evidences,
      evidenceDetailContext.citedEvidenceIndexes(duplicateCitations)
    )
  ).toEqual([
    { evidence: evidences[2], evidenceIndex: 2, displayIndex: 0 }
  ]);
});

test("无效引用索引不生成引用视图且不会自动选中证据", () => {
  const invalidCitations: Citation[] = [0, -1, 1.5, 4].map(
    (evidenceIndex) => ({
      ...citations[0],
      evidence_index: evidenceIndex
    })
  );

  expect(
    evidenceDetailContext.citedEvidenceEntries(
      evidences,
      evidenceDetailContext.citedEvidenceIndexes(invalidCitations)
    )
  ).toEqual([]);
  expect(
    evidenceDetailContext.firstEvidenceKey(
      "turn-1",
      invalidCitations,
      evidences
    )
  ).toBeNull();
});
