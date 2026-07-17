// 证据正文解析：chunk_builder.py 把结构化知识拼成“字段名：值”行文本，
// 这里按行还原成字段段落，供明细区分大模型归纳内容与案例原文着色。
// 白名单与 chunk_builder 的字段名保持同步，未识别的行按纯文本展示。

export type FieldOrigin = "generated" | "source";

export type EvidenceTextSegment =
  | { kind: "field"; label: string; value: string; origin: FieldOrigin }
  | { kind: "block"; label: string; items: string[]; origin: FieldOrigin }
  | { kind: "plain"; value: string };

// 值来自案例素材原文的字段；其余白名单字段视为大模型归纳。
const SOURCE_FIELD_LABELS = new Set(["来源原文", "原始话术"]);

const GENERATED_FIELD_LABELS = new Set([
  "案例",
  "知识类型",
  "标签",
  "阶段",
  "客户状态",
  "销售目标",
  "关键动作",
  "策略名称",
  "别名",
  "定义",
  "适用阶段",
  "步骤",
  "建议做法",
  "避免做法",
  "置信度",
  "模型归纳",
  "摘要",
  "关键要点",
  "话术 ID",
  "场景",
  "客户触发点",
  "目标",
  "教练推荐话术",
  "关联策略",
  "追问建议",
  "合规提醒",
  "客户异议",
  "异议诊断",
  "推荐回应",
  "关联话术"
]);

function fieldOrigin(label: string): FieldOrigin | null {
  if (SOURCE_FIELD_LABELS.has(label)) {
    return "source";
  }
  if (GENERATED_FIELD_LABELS.has(label)) {
    return "generated";
  }
  return null;
}

export function parseEvidenceText(text: string): EvidenceTextSegment[] {
  const segments: EvidenceTextSegment[] = [];
  let currentBlock: Extract<EvidenceTextSegment, { kind: "block" }> | null = null;

  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) {
      continue;
    }
    if (currentBlock) {
      if (trimmed.startsWith("- ")) {
        currentBlock.items.push(trimmed.slice(2));
        continue;
      }
      // 块内非 bullet 行：只要不是新的白名单字段，就当作上一条原文的续行
      // （原文摘录常跨多行），归入同一条，避免脱离折叠块变成独立段落。
      const separator = trimmed.indexOf("：");
      const fieldLabel = separator > 0 ? trimmed.slice(0, separator) : "";
      const isNewField = Boolean(fieldLabel && fieldOrigin(fieldLabel));
      if (!isNewField && currentBlock.items.length > 0) {
        const lastIndex = currentBlock.items.length - 1;
        currentBlock.items[lastIndex] =
          `${currentBlock.items[lastIndex]}\n${trimmed}`;
        continue;
      }
    }
    const separatorIndex = trimmed.indexOf("：");
    const label = separatorIndex > 0 ? trimmed.slice(0, separatorIndex) : "";
    const origin = label ? fieldOrigin(label) : null;
    if (origin) {
      const value = trimmed.slice(separatorIndex + 1);
      if (value === "") {
        currentBlock = { kind: "block", label, items: [], origin };
        segments.push(currentBlock);
      } else {
        currentBlock = null;
        segments.push({ kind: "field", label, value, origin });
      }
      continue;
    }
    currentBlock = null;
    segments.push({ kind: "plain", value: trimmed });
  }
  return segments;
}

// 只要解析出任一字段段落，就按结构化视图渲染；否则回退纯文本。
export function hasStructuredFields(segments: EvidenceTextSegment[]): boolean {
  return segments.some((segment) => segment.kind !== "plain");
}
