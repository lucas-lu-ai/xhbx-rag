import {
  dedupeCitations,
  evidenceComplianceRisks,
  formatChunkType,
  formatEvidenceSourceCompact,
  formatProcessPayload,
  formatTagBoost
} from "./format";
import type { Citation } from "./types";

function citation(overrides: Partial<Citation> = {}): Citation {
  return {
    filename: "第3节.docx",
    source_path: "案例/第3节.docx",
    display_location: "L35 · 主题：保单整理",
    display_excerpt: "案例：客户年保费控制在80万以内。",
    anchor_id: "docx:第3节.docx#line-35",
    can_reveal: true,
    ...overrides
  };
}

test("dedupeCitations 折叠逐字相同的引用", () => {
  const result = dedupeCitations([citation(), citation(), citation()]);

  expect(result).toHaveLength(1);
});

test("dedupeCitations 保留不同副本文件（source_path 不同）", () => {
  const result = dedupeCitations([
    citation({
      filename: "课件.pptx",
      source_path: "模块一/课件.pptx",
      anchor_id: "pptx:课件.pptx#line-25"
    }),
    citation({
      filename: "课件.pptx",
      source_path: "模块二/课件 - 副本.pptx",
      anchor_id: "pptx:课件 - 副本.pptx#line-25"
    })
  ]);

  expect(result).toHaveLength(2);
});

test("dedupeCitations 保留同文件不同定位", () => {
  const result = dedupeCitations([
    citation({ display_location: "L35", anchor_id: "docx:a#line-35" }),
    citation({ display_location: "L97", anchor_id: "docx:a#line-97" })
  ]);

  expect(result).toHaveLength(2);
});

test("dedupeCitations 保持首次出现顺序", () => {
  const first = citation({ display_location: "L10", anchor_id: "a#10" });
  const second = citation({ display_location: "L20", anchor_id: "a#20" });
  const result = dedupeCitations([first, second, citation({ ...first })]);

  expect(result).toEqual([first, second]);
});

test("formatEvidenceSourceCompact 丢弃章节路径只保留短定位段", () => {
  expect(
    formatEvidenceSourceCompact({
      filename: "第3节：准客户档案（下）.docx",
      display_location:
        "L231-L233 · 课程主题：客户档案成就保险生涯（完整版） / 模块三：两套档案",
      display_excerpt: "",
      can_reveal: true
    })
  ).toBe("第3节：准客户档案（下）.docx · L231-L233");
  expect(
    formatEvidenceSourceCompact({
      filename: "课件.pptx",
      display_location: "slide12 · 模块一：开场",
      display_excerpt: "",
      can_reveal: true
    })
  ).toBe("课件.pptx · slide12");
});

test("formatEvidenceSourceCompact 保持案例短定位与缺省行为", () => {
  expect(
    formatEvidenceSourceCompact({
      filename: "第2节.track-0.txt",
      display_location: "L1",
      display_excerpt: "",
      can_reveal: true
    })
  ).toBe("第2节.track-0.txt · L1");
  expect(
    formatEvidenceSourceCompact({
      filename: "文档.docx",
      display_location: "未提供精确位置",
      display_excerpt: "",
      can_reveal: false
    })
  ).toBe("文档.docx");
  // 只有章节路径没有短定位段时，退回仅文件名。
  expect(
    formatEvidenceSourceCompact({
      filename: "教材.docx",
      display_location: "课程主题：客户档案 / 模块三",
      display_excerpt: "",
      can_reveal: false
    })
  ).toBe("教材.docx");
});

test("formatChunkType 中文化已知类型并回退原值", () => {
  expect(formatChunkType("objection_handling")).toBe("异议处理");
  expect(formatChunkType("script")).toBe("销售话术");
  expect(formatChunkType("strategy")).toBe("销售策略");
  expect(formatChunkType("customer_journey")).toBe("客户旅程");
  expect(formatChunkType("training_course")).toBe("培训课程");
  expect(formatChunkType("unknown_type")).toBe("unknown_type");
  expect(formatChunkType(undefined)).toBe("未知类型");
});

test("formatProcessPayload 显示标签加权摘要", () => {
  expect(
    formatProcessPayload({
      query_tag_paths: ["客户需求/保费预算", "异议类型/预算异议"],
      boosted_count: 1,
      boosted: []
    })
  ).toBe("识别标签 2 个 · 提权证据 1 条");
});

test("formatProcessPayload 保持既有字段的展示", () => {
  expect(formatProcessPayload({ rewritten_query: "预算不超过80万" })).toBe(
    "改写为：预算不超过80万"
  );
  expect(formatProcessPayload({ candidate_count: 3 })).toBe("候选 3 条");
  expect(formatProcessPayload({})).toBe("");
});

test("formatTagBoost 只在实际提权时返回倍数", () => {
  expect(formatTagBoost(1.2)).toBe("×1.2");
  expect(formatTagBoost(1)).toBe("");
  expect(formatTagBoost(undefined)).toBe("");
  expect(formatTagBoost("1.2")).toBe("");
});

test("evidenceComplianceRisks 从 metadata 提取合规风险列表", () => {
  expect(
    evidenceComplianceRisks({ compliance_risks: ["收益承诺风险", "适当性风险"] })
  ).toEqual(["收益承诺风险", "适当性风险"]);
  expect(evidenceComplianceRisks({ compliance_risks: [] })).toEqual([]);
  expect(evidenceComplianceRisks({ compliance_risks: "不是数组" })).toEqual([]);
  expect(evidenceComplianceRisks({})).toEqual([]);
  expect(evidenceComplianceRisks(undefined)).toEqual([]);
});
