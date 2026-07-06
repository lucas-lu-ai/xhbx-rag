import { formatProcessPayload, formatTagBoost } from "./format";

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
