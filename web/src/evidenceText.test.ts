import {
  hasStructuredFields,
  parseEvidenceText
} from "./evidenceText";

test("解析异议处理正文为字段段落并区分生成与原文", () => {
  const text = [
    "案例：案例A",
    "知识类型：异议处理",
    "客户异议：客户说每年不能超过80万",
    "异议诊断：预算顾虑背后是保障优先级不清晰",
    "推荐回应：先承接预算，再对齐保障缺口",
    "关联策略：预算锚定法、缺口分析",
    "来源原文：",
    "- 第2节.track-0.txt：客户说每年保费预算不能超过80万",
    "- 第3节.track-0.txt：先看家庭责任额度"
  ].join("\n");

  const segments = parseEvidenceText(text);

  expect(segments).toEqual([
    { kind: "field", label: "案例", value: "案例A", origin: "generated" },
    { kind: "field", label: "知识类型", value: "异议处理", origin: "generated" },
    {
      kind: "field",
      label: "客户异议",
      value: "客户说每年不能超过80万",
      origin: "generated"
    },
    {
      kind: "field",
      label: "异议诊断",
      value: "预算顾虑背后是保障优先级不清晰",
      origin: "generated"
    },
    {
      kind: "field",
      label: "推荐回应",
      value: "先承接预算，再对齐保障缺口",
      origin: "generated"
    },
    {
      kind: "field",
      label: "关联策略",
      value: "预算锚定法、缺口分析",
      origin: "generated"
    },
    {
      kind: "block",
      label: "来源原文",
      items: [
        "第2节.track-0.txt：客户说每年保费预算不能超过80万",
        "第3节.track-0.txt：先看家庭责任额度"
      ],
      origin: "source"
    }
  ]);
  expect(hasStructuredFields(segments)).toBe(true);
});

test("原始话术按原文标记，bullet 块归属块标题", () => {
  const text = [
    "知识类型：场景话术",
    "原始话术：每年80万完全没问题",
    "教练推荐话术：我们先看保障缺口，再定预算",
    "追问建议：",
    "- 您最关注家庭哪方面的保障？"
  ].join("\n");

  const segments = parseEvidenceText(text);

  expect(segments).toContainEqual({
    kind: "field",
    label: "原始话术",
    value: "每年80万完全没问题",
    origin: "source"
  });
  expect(segments).toContainEqual({
    kind: "field",
    label: "教练推荐话术",
    value: "我们先看保障缺口，再定预算",
    origin: "generated"
  });
  expect(segments).toContainEqual({
    kind: "block",
    label: "追问建议",
    items: ["您最关注家庭哪方面的保障？"],
    origin: "generated"
  });
});

test("来源原文的多行原文续行归入同一条，不脱离折叠块", () => {
  const text = [
    "推荐回应：先承接预算",
    "来源原文：",
    "- 第2节.docx：客户说太贵了",
    "讲师：那我们分批买",
    "客户说这样也可以",
    "- 第3节.txt：先看保障缺口"
  ].join("\n");

  const segments = parseEvidenceText(text);
  const block = segments.find((segment) => segment.kind === "block");

  expect(block).toEqual({
    kind: "block",
    label: "来源原文",
    origin: "source",
    items: [
      "第2节.docx：客户说太贵了\n讲师：那我们分批买\n客户说这样也可以",
      "第3节.txt：先看保障缺口"
    ]
  });
  // 续行没有被拆成独立 plain 段落。
  expect(segments.filter((segment) => segment.kind === "plain")).toHaveLength(0);
});

test("非白名单前缀与普通文本按纯文本处理", () => {
  const text = [
    "这是一段没有字段结构的课程内容。",
    "注意事项：这里的冒号前缀不在白名单里。",
    "- 孤立的列表项没有归属块"
  ].join("\n");

  const segments = parseEvidenceText(text);

  expect(segments).toEqual([
    { kind: "plain", value: "这是一段没有字段结构的课程内容。" },
    { kind: "plain", value: "注意事项：这里的冒号前缀不在白名单里。" },
    { kind: "plain", value: "- 孤立的列表项没有归属块" }
  ]);
  expect(hasStructuredFields(segments)).toBe(false);
});

test("tag_chunk 插入的标签行按 AI 归纳字段解析", () => {
  const segments = parseEvidenceText(
    "标签：销售阶段/需求分析；客户画像/高净值客户"
  );

  expect(segments).toEqual([
    {
      kind: "field",
      label: "标签",
      value: "销售阶段/需求分析；客户画像/高净值客户",
      origin: "generated"
    }
  ]);
});

test("空行被跳过，字段值里的冒号不影响解析", () => {
  const text = "定义：先谈价值：再谈价格\n\n置信度：high";

  const segments = parseEvidenceText(text);

  expect(segments).toEqual([
    {
      kind: "field",
      label: "定义",
      value: "先谈价值：再谈价格",
      origin: "generated"
    },
    { kind: "field", label: "置信度", value: "high", origin: "generated" }
  ]);
});
