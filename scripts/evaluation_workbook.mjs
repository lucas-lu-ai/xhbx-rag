import fs from "node:fs/promises";
import path from "node:path";

import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";


const RESULT_HEADERS = [
  "智能体回答",
  "事实正确性得分",
  "关键点覆盖得分",
  "证据忠实性得分",
  "引用及黄金来源命中得分",
  "相关性与表达得分",
  "总分",
  "评测等级",
  "耗时（秒）",
  "主chunk命中",
  "黄金chunk召回率",
  "检索chunk_id",
  "扣分原因",
  "错误标签",
  "改进建议",
  "评测状态",
];

const RESULT_KEYS = ["Excel行号", ...RESULT_HEADERS];
const REPORT_SHEET_NAMES = ["评测总览", "低分与错误案例", "运行元数据"];
const SCORE_LIMITS = new Map([
  ["事实正确性得分", 35],
  ["关键点覆盖得分", 20],
  ["证据忠实性得分", 20],
  ["引用及黄金来源命中得分", 15],
  ["相关性与表达得分", 10],
]);
const JUDGE_SCORE_FIELDS = [
  "事实正确性得分",
  "关键点覆盖得分",
  "证据忠实性得分",
  "相关性与表达得分",
];
const RESULT_TEXT_FIELDS = [
  "智能体回答",
  "检索chunk_id",
  "扣分原因",
  "错误标签",
  "改进建议",
];
const VALID_GRADES = new Set([
  "优秀", "合格", "不合格", "问答失败", "评测失败",
]);
const VALID_STATUSES = new Set(["已完成", "问答失败", "评测失败"]);
const VALID_ERROR_TAGS = new Set([
  "事实错误",
  "关键点缺失",
  "无依据扩写",
  "答非所问",
  "引用缺失",
  "检索未命中",
  "问答执行失败",
  "裁判执行失败",
]);
const LOW_SCORE_ERROR_TAGS = ["无依据扩写", "引用缺失", "检索未命中"];
const SAFE_METADATA_KEYS = [
  "运行ID",
  "输入文件名",
  "输入SHA256",
  "Git提交",
  "问答模型名",
  "裁判模型名",
  "同模型裁判",
  "初检候选数",
  "最终证据数",
  "问答并发数",
  "裁判并发数",
  "评分版本",
  "Docker Milvus地址",
  "知识集合统计",
];


function parseArguments(values) {
  const options = new Map();
  for (let index = 0; index < values.length; index += 2) {
    const name = values[index];
    const value = values[index + 1];
    if (!name?.startsWith("--") || value === undefined) {
      throw new Error("命令行参数必须使用 --名称 值 的格式");
    }
    options.set(name.slice(2), value);
  }
  return options;
}


function requiredOption(options, name) {
  const value = options.get(name);
  if (!value) {
    throw new Error(`缺少命令行参数：--${name}`);
  }
  return value;
}


function requiredWorksheet(workbook, name) {
  try {
    return workbook.worksheets.getItem(name);
  } catch {
    throw new Error(`缺少工作表：${name}`);
  }
}


function optionalWorksheet(workbook, name) {
  try {
    return workbook.worksheets.getItem(name);
  } catch {
    return null;
  }
}


function requiredMainWorksheet(workbook) {
  const candidates = ["绩优案例测试-楚琦", "绩优案例测试"]
    .map((name) => ({ name, sheet: optionalWorksheet(workbook, name) }))
    .filter((candidate) => candidate.sheet !== null);
  if (candidates.length === 0) {
    throw new Error("缺少主表：绩优案例测试-楚琦 或 绩优案例测试");
  }
  if (candidates.length !== 1) {
    throw new Error("主表名称存在歧义：两个候选工作表同时存在");
  }
  return candidates[0];
}


function normalizedText(value) {
  return String(value ?? "").trim();
}


function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}


function requirePayloadObject(value, name) {
  if (!isPlainObject(value)) {
    throw new Error(`${name}必须是对象`);
  }
  return value;
}


function requireFiniteNumber(
  value,
  fieldName,
  excelRow,
  minimum,
  maximum = null,
) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`逐题结果第 ${excelRow} 行的${fieldName}必须是有限数值`);
  }
  if (value < minimum || (maximum !== null && value > maximum)) {
    const range = maximum === null
      ? `大于等于 ${minimum}`
      : `在 ${minimum} 到 ${maximum}`;
    throw new Error(`逐题结果第 ${excelRow} 行的${fieldName}必须${range}`);
  }
  return value;
}


function requireNullableScore(row, fieldName, excelRow) {
  const value = row[fieldName];
  if (value === null) {
    return null;
  }
  return requireFiniteNumber(
    value,
    fieldName,
    excelRow,
    0,
    SCORE_LIMITS.get(fieldName),
  );
}


function gradeForTotal(total) {
  if (total >= 85) {
    return "优秀";
  }
  if (total >= 75) {
    return "合格";
  }
  return "不合格";
}


function requireCanonicalErrorTags(value, excelRow) {
  const normalized = normalizedText(value);
  if (normalized === "") {
    return "";
  }
  if (normalized.includes(",") || normalized.includes("，")) {
    throw new Error(
      `逐题结果第 ${excelRow} 行的错误标签必须使用分号分隔`,
    );
  }
  const tags = normalized.split(/[；;]/).map((tag) => tag.trim());
  if (tags.some((tag) => tag === "")) {
    throw new Error(`逐题结果第 ${excelRow} 行的错误标签不得包含空片段`);
  }
  const seen = new Set();
  for (const tag of tags) {
    if (!VALID_ERROR_TAGS.has(tag)) {
      throw new Error(
        `逐题结果第 ${excelRow} 行的错误标签包含未知标签：${tag}`,
      );
    }
    if (seen.has(tag)) {
      throw new Error(`逐题结果第 ${excelRow} 行的错误标签不得重复：${tag}`);
    }
    seen.add(tag);
  }
  return tags.join("；");
}


function requireCanonicalResultRow(rawRow) {
  const row = requirePayloadObject(rawRow, "逐题结果记录");
  const excelRow = row["Excel行号"];
  if (!Number.isInteger(excelRow) || excelRow < 2 || excelRow > 51) {
    throw new Error(`逐题结果的Excel行号无效：${excelRow ?? ""}`);
  }
  for (const key of RESULT_KEYS) {
    if (!Object.hasOwn(row, key)) {
      throw new Error(`逐题结果第 ${excelRow} 行缺少字段：${key}`);
    }
  }
  for (const fieldName of RESULT_TEXT_FIELDS) {
    if (typeof row[fieldName] !== "string") {
      throw new Error(`逐题结果第 ${excelRow} 行的${fieldName}必须是字符串`);
    }
  }

  const grade = normalizedText(row["评测等级"]);
  const status = normalizedText(row["评测状态"]);
  if (!VALID_GRADES.has(grade)) {
    throw new Error(`逐题结果第 ${excelRow} 行的评测等级无效：${grade}`);
  }
  if (!VALID_STATUSES.has(status)) {
    throw new Error(`逐题结果第 ${excelRow} 行的评测状态无效：${status}`);
  }
  requireFiniteNumber(row["耗时（秒）"], "耗时（秒）", excelRow, 0);

  const primaryHit = row["主chunk命中"];
  if (
    primaryHit !== null
    && !["是", "否"].includes(normalizedText(primaryHit))
  ) {
    throw new Error(`逐题结果第 ${excelRow} 行的主chunk命中无效`);
  }
  const recall = row["黄金chunk召回率"];
  if (recall !== null) {
    requireFiniteNumber(recall, "黄金chunk召回率", excelRow, 0, 1);
  }
  if ((primaryHit === null) !== (recall === null)) {
    throw new Error(
      `逐题结果第 ${excelRow} 行的` +
      "主chunk命中与黄金chunk召回率必须同时为空或同时有值",
    );
  }

  const normalizedRow = {
    ...row,
    "评测等级": grade,
    "评测状态": status,
    "主chunk命中": primaryHit === null ? null : normalizedText(primaryHit),
    "错误标签": requireCanonicalErrorTags(row["错误标签"], excelRow),
  };
  if (status === "已完成") {
    const scores = [...SCORE_LIMITS.keys()].map(
      (fieldName) => requireFiniteNumber(
        row[fieldName],
        fieldName,
        excelRow,
        0,
        SCORE_LIMITS.get(fieldName),
      ),
    );
    const total = requireFiniteNumber(row["总分"], "总分", excelRow, 0, 100);
    const scoreSum = scores.reduce((sum, value) => sum + value, 0);
    const expectedTotal = Math.round(scoreSum * 100) / 100;
    if (Math.abs(total - expectedTotal) > 1e-9) {
      throw new Error(`逐题结果第 ${excelRow} 行的总分必须等于五维得分之和`);
    }
    if (grade !== gradeForTotal(total)) {
      throw new Error(`逐题结果第 ${excelRow} 行的评测等级与总分不一致`);
    }
    return normalizedRow;
  }

  if (grade !== status) {
    throw new Error(`逐题结果第 ${excelRow} 行的失败等级必须与评测状态一致`);
  }
  for (const fieldName of JUDGE_SCORE_FIELDS) {
    if (row[fieldName] !== null) {
      throw new Error(`逐题结果第 ${excelRow} 行的${status}不得包含语义或证据分数`);
    }
  }
  if (status === "问答失败") {
    if (
      row["引用及黄金来源命中得分"] !== null
      || row["总分"] !== 0
      || primaryHit !== null
      || recall !== null
      || normalizedText(row["检索chunk_id"]) !== ""
    ) {
      throw new Error(`逐题结果第 ${excelRow} 行的问答失败不得包含语义或证据分数`);
    }
    return normalizedRow;
  }

  const deterministicScore = requireNullableScore(
    row,
    "引用及黄金来源命中得分",
    excelRow,
  );
  if (row["总分"] !== null) {
    throw new Error(`逐题结果第 ${excelRow} 行的评测失败总分必须为空`);
  }
  if (
    deterministicScore === null
    && (
      primaryHit !== null
      || recall !== null
      || normalizedText(row["检索chunk_id"]) !== ""
    )
  ) {
    throw new Error(`逐题结果第 ${excelRow} 行不得伪造未测量的证据指标`);
  }
  return normalizedRow;
}


function requirePayloadRows(payload) {
  if (!Array.isArray(payload["逐题结果"]) || payload["逐题结果"].length !== 50) {
    throw new Error("逐题结果必须包含 50 条记录");
  }
  const rowsByNumber = new Map();
  for (const rawRow of payload["逐题结果"]) {
    const row = requireCanonicalResultRow(rawRow);
    const excelRow = row["Excel行号"];
    if (rowsByNumber.has(excelRow)) {
      throw new Error(`逐题结果的Excel行号重复：${excelRow}`);
    }
    rowsByNumber.set(excelRow, row);
  }
  for (let excelRow = 2; excelRow <= 51; excelRow += 1) {
    if (!rowsByNumber.has(excelRow)) {
      throw new Error(`逐题结果缺少Excel行号：${excelRow}`);
    }
  }
  return rowsByNumber;
}


function requireEvidenceMetrics(summary) {
  const metrics = requirePayloadObject(summary["证据指标"], "汇总指标.证据指标");
  const primaryRate = requireFiniteNumber(
    metrics["主chunk命中率"],
    "汇总指标.证据指标.主chunk命中率",
    "汇总",
    0,
    1,
  );
  const recallRate = requireFiniteNumber(
    metrics["平均黄金chunk召回率"],
    "汇总指标.证据指标.平均黄金chunk召回率",
    "汇总",
    0,
    1,
  );
  requireFiniteNumber(
    metrics["平均引用及黄金来源命中得分"],
    "汇总指标.证据指标.平均引用及黄金来源命中得分",
    "汇总",
    0,
    15,
  );
  return { primaryRate, recallRate };
}


function safeCellValue(value) {
  if (value === null || value === undefined) {
    return null;
  }
  if (["string", "number", "boolean"].includes(typeof value)) {
    return value;
  }
  return JSON.stringify(value);
}


function resetReportSheet(workbook, name) {
  const sheet = workbook.worksheets.getOrAdd(name);
  sheet.deleteAllDrawings();
  const usedRange = sheet.getUsedRange();
  if (usedRange) {
    usedRange.unmerge();
    usedRange.clear({ applyTo: "all" });
  }
  return sheet;
}


function applySectionHeader(range) {
  range.format = {
    fill: "#1F4E78",
    font: { bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "outside", style: "thin", color: "#A6A6A6" },
  };
}


function applyBodyTable(range) {
  range.format = {
    verticalAlignment: "top",
    wrapText: true,
    borders: {
      insideHorizontal: { style: "thin", color: "#D9E2F3" },
      bottom: { style: "thin", color: "#A6A6A6" },
      left: { style: "thin", color: "#A6A6A6" },
      right: { style: "thin", color: "#A6A6A6" },
    },
  };
}


function setColumnWidth(sheet, column, lastRow, width) {
  sheet.getRange(`${column}1:${column}${lastRow}`).format.columnWidth = width;
}


function writeOverviewSheet(sheet, payload, mainSheetName) {
  const summary = requirePayloadObject(payload["汇总指标"], "汇总指标");
  const evidenceMetrics = requireEvidenceMetrics(summary);
  sheet.getRange("A1:H1").merge();
  sheet.getRange("A1").values = [["问答智能体评测总览"]];
  sheet.getRange("A3:B7").values = [
    ["指标", "结果"],
    ["总题数", null],
    ["平均分", null],
    ["合格率", null],
    ["优秀率", null],
  ];
  const escapedSheetName = mainSheetName.replaceAll("'", "''");
  sheet.getRange("B4").formulas = [
    [`=COUNTA('${escapedSheetName}'!$U$2:$U$51)`],
  ];
  sheet.getRange("B5").values = [[safeCellValue(summary["平均分"])]];
  sheet.getRange("B6:B7").formulas = [
    [`=COUNTIF('${escapedSheetName}'!$M$2:$M$51,"优秀")/B4+COUNTIF('${escapedSheetName}'!$M$2:$M$51,"合格")/B4`],
    [`=COUNTIF('${escapedSheetName}'!$M$2:$M$51,"优秀")/B4`],
  ];
  sheet.getRange("A9:B15").values = [
    ["补充指标", "结果"],
    ["保守通过率", safeCellValue(summary["保守通过率"])],
    ["有效通过率", safeCellValue(summary["有效通过率"])],
    ["问答成功率", safeCellValue(summary["问答成功率"])],
    ["分数P50", safeCellValue(summary["分数P50"])],
    ["分数P95", safeCellValue(summary["分数P95"])],
    ["同模型裁判", payload["运行信息"]?.["同模型裁判"] === true ? "是" : "否"],
  ];

  sheet.getRange("D3:E9").values = [
    ["评分维度", "平均得分"],
    ["事实正确性", null],
    ["关键点覆盖", null],
    ["证据忠实性", null],
    ["引用及黄金来源命中", null],
    ["相关性与表达", null],
    ["总分", null],
  ];
  sheet.getRange("E4:E8").formulas = [
    [`=IFERROR(AVERAGE('${escapedSheetName}'!$G$2:$G$51),0)`],
    [`=IFERROR(AVERAGE('${escapedSheetName}'!$H$2:$H$51),0)`],
    [`=IFERROR(AVERAGE('${escapedSheetName}'!$I$2:$I$51),0)`],
    [`=IFERROR(AVERAGE('${escapedSheetName}'!$J$2:$J$51),0)`],
    [`=IFERROR(AVERAGE('${escapedSheetName}'!$K$2:$K$51),0)`],
  ];
  sheet.getRange("E9").values = [[safeCellValue(summary["平均分"])]];

  sheet.getRange("G3:H7").values = [
    ["运行质量", "结果"],
    ["平均耗时（秒）", null],
    ["主chunk命中率", evidenceMetrics.primaryRate],
    ["平均黄金chunk召回率", evidenceMetrics.recallRate],
    ["已完成题数", null],
  ];
  sheet.getRange("H4").formulas = [
    [`=IFERROR(AVERAGE('${escapedSheetName}'!$N$2:$N$51),0)`],
  ];
  sheet.getRange("H7").formulas = [
    [`=COUNTIF('${escapedSheetName}'!$U$2:$U$51,"已完成")`],
  ];

  const layers = isPlainObject(summary["溯源状态分层"])
    ? summary["溯源状态分层"]
    : {};
  const layerRows = [
    ["溯源状态", "数量", "平均分", "通过率"],
    ...["完整支持", "部分支持", "未定位"].map((status) => {
      const layer = isPlainObject(layers[status]) ? layers[status] : {};
      return [
        status,
        safeCellValue(layer["数量"] ?? 0),
        safeCellValue(layer["平均分"] ?? 0),
        safeCellValue(layer["通过率"] ?? 0),
      ];
    }),
  ];
  sheet.getRange("A18:D21").values = layerRows;

  const errorCounts = isPlainObject(summary["错误标签频次"])
    ? Object.entries(summary["错误标签频次"])
    : [];
  const errorRows = [
    ["主要错误标签", "题数"],
    ...(errorCounts.length > 0 ? errorCounts : [["未发现固定错误标签", 0]]),
  ];
  sheet.getRangeByIndexes(17, 5, errorRows.length, 2).values = errorRows;

  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(3);
  sheet.getRange("A1:H1").format = {
    fill: "#17365D",
    font: { bold: true, color: "#FFFFFF", size: 18 },
    horizontalAlignment: "left",
    verticalAlignment: "center",
  };
  sheet.getRange("A1:H1").format.rowHeight = 32;
  for (const rangeAddress of ["A3:B3", "A9:B9", "D3:E3", "G3:H3", "A18:D18", "F18:G18"]) {
    applySectionHeader(sheet.getRange(rangeAddress));
  }
  for (const rangeAddress of ["A4:B7", "A10:B15", "D4:E9", "G4:H7", "A19:D21"]) {
    applyBodyTable(sheet.getRange(rangeAddress));
  }
  if (errorRows.length > 1) {
    applyBodyTable(sheet.getRangeByIndexes(18, 5, errorRows.length - 1, 2));
  }
  sheet.getRange("B5:B5").format.numberFormat = "0.00";
  sheet.getRange("B6:B7").format.numberFormat = "0.0%";
  sheet.getRange("B10:B12").format.numberFormat = "0.0%";
  sheet.getRange("B13:B14").format.numberFormat = "0.00";
  sheet.getRange("E4:E9").format.numberFormat = "0.00";
  sheet.getRange("H4:H4").format.numberFormat = "0.00";
  sheet.getRange("H5:H6").format.numberFormat = "0.0%";
  sheet.getRange("D19:D21").format.numberFormat = "0.0%";
  setColumnWidth(sheet, "A", 21, 20);
  setColumnWidth(sheet, "B", 21, 16);
  setColumnWidth(sheet, "C", 21, 4);
  setColumnWidth(sheet, "D", 21, 24);
  setColumnWidth(sheet, "E", 21, 16);
  setColumnWidth(sheet, "F", Math.max(21, 17 + errorRows.length), 28);
  setColumnWidth(sheet, "G", Math.max(21, 17 + errorRows.length), 18);
  setColumnWidth(sheet, "H", 21, 18);
}


function shouldIncludeLowScore(row) {
  const errorTags = normalizedText(row["错误标签"]);
  return (
    !["优秀", "合格"].includes(normalizedText(row["评测等级"]))
    || normalizedText(row["评测状态"]) !== "已完成"
    || LOW_SCORE_ERROR_TAGS.some((tag) => errorTags.includes(tag))
    || (
      normalizedText(row["评测状态"]) === "已完成"
      && normalizedText(row["检索chunk_id"]) === ""
    )
  );
}


function writeLowScoreSheet(sheet, rowsByNumber) {
  const headers = ["Excel行号", ...RESULT_HEADERS];
  const rows = [...rowsByNumber.values()]
    .filter(shouldIncludeLowScore)
    .map((row) => headers.map((header) => safeCellValue(row[header])));
  sheet.getRangeByIndexes(0, 0, 1, headers.length).values = [headers];
  if (rows.length > 0) {
    sheet.getRangeByIndexes(1, 0, rows.length, headers.length).values = rows;
  }
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  applySectionHeader(sheet.getRangeByIndexes(0, 0, 1, headers.length));
  if (rows.length > 0) {
    applyBodyTable(sheet.getRangeByIndexes(1, 0, rows.length, headers.length));
    sheet.getRangeByIndexes(1, 7, rows.length, 1).format.numberFormat = "0.00";
    sheet.getRangeByIndexes(1, 9, rows.length, 1).format.numberFormat = "0.00";
    sheet.getRangeByIndexes(1, 11, rows.length, 1).format.numberFormat = "0.0%";
  }
  const widths = [11, 42, 14, 14, 14, 22, 14, 12, 12, 12, 12, 16, 25, 36, 22, 36, 12];
  const columns = "ABCDEFGHIJKLMNOPQ";
  widths.forEach((width, index) => setColumnWidth(
    sheet,
    columns[index],
    Math.max(1, rows.length + 1),
    width,
  ));
  sheet.getUsedRange().format.autofitRows();
}


function looksLikeAbsoluteUserPath(value) {
  const text = normalizedText(value).replaceAll("\\", "/");
  return (
    /^\/+(?:Users|home)\//i.test(text)
    || /^\/?[A-Za-z]:\/Users\//i.test(text)
  );
}


function decodedUriPath(value) {
  let decoded = value;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const next = decodeURIComponent(decoded);
      if (next === decoded) {
        return decoded;
      }
      decoded = next;
    } catch {
      return decoded;
    }
  }
  return decoded;
}


function credentialKeyTokens(value) {
  return String(value)
    .replaceAll(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replaceAll(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .toLowerCase()
    .match(/[a-z0-9]+/g) ?? [];
}


function looksLikeCredentialKey(value) {
  const text = String(value);
  if (/(?:密钥|令牌|密码|口令|凭证)/.test(text)) {
    return true;
  }
  if (
    /(?:^|[^a-z0-9])(?:token|secret|password|passwd|credential|authorization)(?:$|[^a-z0-9])/i
      .test(text)
  ) {
    return true;
  }
  const tokens = credentialKeyTokens(text);
  const directSecrets = new Set(["key", "token", "secret", "password"]);
  if (tokens.some((token) => directSecrets.has(token))) {
    return true;
  }
  const credentialKeyPrefixes = [
    "private", "api", "client", "access", "auth", "signing",
    "encryption", "milvus",
  ];
  return tokens.some((token) => (
    token.endsWith("token")
    || token.endsWith("secret")
    || token.endsWith("password")
    || credentialKeyPrefixes.some((prefix) => token.includes(`${prefix}key`))
  ));
}


function assertSafeMetadataValue(value, fieldName) {
  if (typeof value === "string") {
    const text = normalizedText(value);
    if (looksLikeAbsoluteUserPath(text)) {
      throw new Error(`运行元数据不得包含绝对用户目录：${fieldName}`);
    }
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) {
      let parsed;
      try {
        parsed = new URL(text);
      } catch {
        throw new Error(`运行元数据包含无法安全解析的地址：${fieldName}`);
      }
      if (parsed.username || parsed.password) {
        throw new Error(`运行元数据不得包含带凭证的地址：${fieldName}`);
      }
      if (
        parsed.protocol === "file:"
        && looksLikeAbsoluteUserPath(decodedUriPath(parsed.pathname))
      ) {
        throw new Error(`运行元数据不得包含绝对用户目录：${fieldName}`);
      }
    }
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item) => assertSafeMetadataValue(item, fieldName));
    return;
  }
  if (!isPlainObject(value)) {
    return;
  }
  for (const [key, nested] of Object.entries(value)) {
    if (looksLikeCredentialKey(key)) {
      throw new Error(`运行元数据不得包含凭证字段：${fieldName}`);
    }
    assertSafeMetadataValue(nested, fieldName);
  }
}


function writeMetadataSheet(sheet, payload) {
  const runInfo = requirePayloadObject(payload["运行信息"], "运行信息");
  assertSafeMetadataValue(runInfo, "运行信息");
  const rows = [["字段", "值"]];
  for (const key of SAFE_METADATA_KEYS) {
    if (!Object.hasOwn(runInfo, key)) {
      continue;
    }
    const value = typeof runInfo[key] === "boolean"
      ? (runInfo[key] ? "是" : "否")
      : safeCellValue(runInfo[key]);
    rows.push([key, value]);
  }
  const gitCommitRowIndex = rows.findIndex((row) => row[0] === "Git提交");
  if (gitCommitRowIndex >= 0) {
    sheet.getRange(`B${gitCommitRowIndex + 1}`).setNumberFormat("@");
  }
  sheet.getRangeByIndexes(0, 0, rows.length, 2).values = rows;
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  applySectionHeader(sheet.getRange("A1:B1"));
  if (rows.length > 1) {
    applyBodyTable(sheet.getRangeByIndexes(1, 0, rows.length - 1, 2));
  }
  setColumnWidth(sheet, "A", rows.length, 24);
  setColumnWidth(sheet, "B", rows.length, 72);
  sheet.getRange(`B2:B${rows.length}`).format.wrapText = true;
  sheet.getUsedRange().format.autofitRows();
}


function styleMainSheet(sheet) {
  const header = sheet.getRange("F1:U1");
  header.format = {
    fill: "#1F4E78",
    font: { bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#FFFFFF" },
  };
  header.format.rowHeight = 34;
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(2);
  sheet.getRange("G2:L51").format.numberFormat = "0.00";
  sheet.getRange("N2:N51").format.numberFormat = "0.00";
  sheet.getRange("P2:P51").format.numberFormat = "0.0%";
  sheet.getRange("F2:U51").format.verticalAlignment = "top";
  sheet.getRange("F2:F51").format.wrapText = true;
  sheet.getRange("Q2:U51").format.wrapText = true;
  sheet.getRange("F2:F51").format.horizontalAlignment = "left";
  sheet.getRange("Q2:U51").format.horizontalAlignment = "left";
  sheet.getRange("G2:P51").format.horizontalAlignment = "center";

  const gradeRange = sheet.getRange("M2:M51");
  gradeRange.conditionalFormats.deleteAll();
  gradeRange.conditionalFormats.addCustom('=$M2="优秀"', {
    fill: "#E2F0D9",
    font: { bold: true, color: "#375623" },
  });
  gradeRange.conditionalFormats.addCustom('=$M2="合格"', {
    fill: "#FFF2CC",
    font: { bold: true, color: "#7F6000" },
  });
  for (const failureText of ["不合格", "问答失败", "评测失败"]) {
    gradeRange.conditionalFormats.addCustom(`=$M2="${failureText}"`, {
      fill: "#FCE4D6",
      font: { bold: true, color: "#9C0006" },
    });
  }

  const widths = {
    F: 48, G: 14, H: 14, I: 14, J: 22, K: 16, L: 12, M: 12,
    N: 12, O: 14, P: 16, Q: 26, R: 36, S: 22, T: 36, U: 14,
  };
  for (const [column, width] of Object.entries(widths)) {
    setColumnWidth(sheet, column, 51, width);
  }
  sheet.getRange("F2:U51").format.autofitRows();
}


async function backfill(options) {
  const inputPath = requiredOption(options, "input");
  const payloadPath = requiredOption(options, "payload");
  const outputPath = requiredOption(options, "output");
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
  const { name: mainSheetName, sheet: mainSheet } = requiredMainWorksheet(workbook);
  requiredWorksheet(workbook, "溯源明细");
  const payload = requirePayloadObject(
    JSON.parse(await fs.readFile(payloadPath, "utf8")),
    "工作簿回填载荷",
  );
  const rowsByNumber = requirePayloadRows(payload);
  const resultMatrix = [RESULT_HEADERS];
  for (let excelRow = 2; excelRow <= 51; excelRow += 1) {
    const row = rowsByNumber.get(excelRow);
    resultMatrix.push(RESULT_HEADERS.map((header) => safeCellValue(row[header])));
  }
  mainSheet.getRange("F1:U51").values = resultMatrix;

  const overview = resetReportSheet(workbook, "评测总览");
  const lowScores = resetReportSheet(workbook, "低分与错误案例");
  const metadata = resetReportSheet(workbook, "运行元数据");
  writeOverviewSheet(overview, payload, mainSheetName);
  writeLowScoreSheet(lowScores, rowsByNumber);
  writeMetadataSheet(metadata, payload);
  styleMainSheet(mainSheet);

  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);
}


function deepEqual(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}


function firstMatrixDifference(actual, expected) {
  for (let rowIndex = 0; rowIndex < expected.length; rowIndex += 1) {
    for (
      let columnIndex = 0;
      columnIndex < expected[rowIndex].length;
      columnIndex += 1
    ) {
      const actualValue = normalizedComparableCell(actual[rowIndex]?.[columnIndex]);
      const expectedValue = normalizedComparableCell(expected[rowIndex][columnIndex]);
      if (!deepEqual(actualValue, expectedValue)) {
        return {
          "Excel行号": rowIndex + 2,
          "字段": RESULT_HEADERS[columnIndex],
          "实际值": actualValue,
          "期望值": expectedValue,
        };
      }
    }
  }
  return null;
}


function normalizedComparableCell(value) {
  return value === null || value === undefined || value === "" ? null : value;
}


function countFormulaErrors(workbook, sheetNames) {
  const pattern = /#(?:REF!|DIV\/0!|VALUE!|NAME\?|N\/A)/g;
  let count = 0;
  for (const sheetName of sheetNames) {
    const sheet = requiredWorksheet(workbook, sheetName);
    const usedRange = sheet.getUsedRange();
    if (!usedRange) {
      continue;
    }
    for (const row of usedRange.values) {
      for (const value of row) {
        if (typeof value === "string") {
          count += value.match(pattern)?.length ?? 0;
        }
      }
    }
  }
  return count;
}


function safePreviewName(index, sheetName) {
  const normalized = sheetName.replaceAll(/[^0-9A-Za-z\u4e00-\u9fff_-]/g, "-");
  return `${String(index + 1).padStart(2, "0")}-${normalized}.png`;
}


async function renderSheets(workbook, sheetNames, previewDir) {
  await fs.rm(previewDir, { recursive: true, force: true });
  await fs.mkdir(previewDir, { recursive: true });
  const errors = [];
  let rendered = 0;
  const previewRanges = new Map([
    [sheetNames[0], "A1:U4"],
    ["低分与错误案例", "A1:Q6"],
  ]);
  for (const [index, sheetName] of sheetNames.entries()) {
    try {
      const previewRange = previewRanges.get(sheetName);
      const preview = await workbook.render(
        previewRange
          ? {
              sheetName,
              range: previewRange,
              scale: 0.5,
              format: "png",
            }
          : {
              sheetName,
              autoCrop: "all",
              scale: 1,
              format: "png",
            },
      );
      await fs.writeFile(
        path.join(previewDir, safePreviewName(index, sheetName)),
        new Uint8Array(await preview.arrayBuffer()),
      );
      rendered += 1;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      errors.push(`${sheetName}：${message}`);
    }
  }
  return { rendered, errors };
}


async function verify(options) {
  const inputPath = requiredOption(options, "input");
  const snapshotPath = requiredOption(options, "snapshot");
  const outputPath = requiredOption(options, "output");
  const previewDir = requiredOption(options, "preview-dir");
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
  const { name: mainSheetName, sheet: mainSheet } = requiredMainWorksheet(workbook);
  const detailSheet = requiredWorksheet(workbook, "溯源明细");
  const snapshotPayload = requirePayloadObject(
    JSON.parse(await fs.readFile(snapshotPath, "utf8")),
    "工作簿快照文件",
  );
  const snapshot = requirePayloadObject(snapshotPayload["工作簿快照"], "工作簿快照");
  const expectedPayload = requirePayloadObject(
    snapshotPayload["回填载荷"],
    "工作簿快照.回填载荷",
  );
  const expectedRowsByNumber = requirePayloadRows(expectedPayload);
  const expectedSummary = requirePayloadObject(expectedPayload["汇总指标"], "汇总指标");
  const expectedEvidenceMetrics = requireEvidenceMetrics(expectedSummary);
  const originalMainRows = snapshot["主表A:E"];
  const originalDetailRows = snapshot["溯源明细"];
  if (!Array.isArray(originalMainRows) || !Array.isArray(originalDetailRows)) {
    throw new Error("工作簿快照缺少主表A:E或溯源明细");
  }

  const expectedSheetNames = [
    mainSheetName,
    "溯源明细",
    ...REPORT_SHEET_NAMES,
  ];
  const actualSheetNames = workbook.worksheets.items.map((sheet) => sheet.name);
  const mainRows = mainSheet.getRange("A1:E51").values;
  const detailRows = detailSheet.getUsedRange().values;
  const headers = mainSheet.getRange("F1:U1").values[0].map(normalizedText);
  const actualResultMatrix = mainSheet.getRange("F2:U51").values;
  const expectedResultMatrix = [];
  for (let excelRow = 2; excelRow <= 51; excelRow += 1) {
    const expectedRow = expectedRowsByNumber.get(excelRow);
    expectedResultMatrix.push(
      RESULT_HEADERS.map((header) => safeCellValue(expectedRow[header])),
    );
  }
  const resultDifference = firstMatrixDifference(
    actualResultMatrix,
    expectedResultMatrix,
  );
  const statuses = mainSheet.getRange("U2:U51").values
    .flat()
    .map(normalizedText)
    .filter(Boolean);
  const mainUsedRows = mainSheet.getUsedRange(true).values.length;
  const overviewSheet = requiredWorksheet(workbook, "评测总览");
  const actualPrimaryRate = overviewSheet.getRange("H5").values[0][0];
  const actualRecallRate = overviewSheet.getRange("H6").values[0][0];
  const actualOverviewAverage = overviewSheet.getRange("B5").values[0][0];
  const actualDimensionTotalAverage = overviewSheet.getRange("E9").values[0][0];
  const expectedAverage = safeCellValue(expectedSummary["平均分"]);
  const metadataSheet = requiredWorksheet(workbook, "运行元数据");
  const metadataRows = metadataSheet.getUsedRange(true)?.values ?? [];
  const gitCommitRowIndex = metadataRows.findIndex(
    (row) => normalizedText(row[0]) === "Git提交",
  );
  const gitCommitRow = gitCommitRowIndex >= 0
    ? metadataRows[gitCommitRowIndex]
    : undefined;
  const expectedRunInfo = requirePayloadObject(expectedPayload["运行信息"], "运行信息");
  const actualGitCommit = normalizedText(gitCommitRow?.[1]);
  const expectedGitCommit = normalizedText(expectedRunInfo["Git提交"]);
  const metadataGitStyleInspection = await workbook.inspect({
    kind: "computedStyle",
    sheetId: "运行元数据",
    range: gitCommitRowIndex >= 0 ? `B${gitCommitRowIndex + 1}` : "B1",
    maxChars: 2500,
  });
  let metadataGitNumberFormat = "";
  try {
    const firstStyleLine = String(metadataGitStyleInspection.ndjson ?? "")
      .trim()
      .split("\n")[0];
    metadataGitNumberFormat = normalizedText(
      JSON.parse(firstStyleLine)?.style?.numberFormat,
    );
  } catch {
    metadataGitNumberFormat = "";
  }
  const lowScoreSheet = requiredWorksheet(workbook, "低分与错误案例");
  const lowScoreUsedRange = lowScoreSheet.getUsedRange(true);
  const actualLowScoreRows = lowScoreUsedRange
    ? lowScoreUsedRange.values
      .slice(1)
      .map((row) => row[0])
      .filter((value) => value !== null && value !== undefined && value !== "")
    : [];
  const expectedLowScoreRows = [...expectedRowsByNumber.values()]
    .filter(shouldIncludeLowScore)
    .map((row) => row["Excel行号"]);

  await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300 },
    summary: "最终公式错误扫描",
  });
  const formulaErrorCount = countFormulaErrors(workbook, expectedSheetNames);
  const previewResult = await renderSheets(workbook, expectedSheetNames, previewDir);

  const checks = {
    "工作表恰好五个且名称正确": (
      actualSheetNames.length === 5
      && expectedSheetNames.every((name) => actualSheetNames.includes(name))
    ),
    "主表名称保持不变": snapshot["主表名称"] === mainSheetName,
    "主表有效行数为51": mainUsedRows === 51,
    "评测表头正确": deepEqual(headers, RESULT_HEADERS),
    "五十条评测状态完整": statuses.length === 50,
    "原始主表保持不变": deepEqual(mainRows, originalMainRows),
    "溯源明细保持不变": deepEqual(detailRows, originalDetailRows),
    "五十条评测结果逐格匹配": resultDifference === null,
    "总览证据指标取自汇总": (
      actualPrimaryRate === expectedEvidenceMetrics.primaryRate
      && actualRecallRate === expectedEvidenceMetrics.recallRate
    ),
    "总览平均分取自汇总": (
      actualOverviewAverage === expectedAverage
      && actualDimensionTotalAverage === expectedAverage
    ),
    "运行元数据Git提交保持文本": (
      expectedGitCommit !== ""
      && typeof gitCommitRow?.[1] === "string"
      && actualGitCommit === expectedGitCommit
      && metadataGitNumberFormat === "@"
    ),
    "低分与错误案例完整": deepEqual(actualLowScoreRows, expectedLowScoreRows),
    "公式错误为零": formulaErrorCount === 0,
    "五张预览图全部生成": (
      previewResult.rendered === 5 && previewResult.errors.length === 0
    ),
  };
  const passed = Object.values(checks).every(Boolean);
  const result = {
    "验证通过": passed,
    "检查项": checks,
    "主表名称": mainSheetName,
    "工作表数量": actualSheetNames.length,
    "工作表名称": actualSheetNames,
    "主表行数": mainUsedRows,
    "评测状态数量": statuses.length,
    "主chunk命中率": actualPrimaryRate,
    "平均黄金chunk召回率": actualRecallRate,
    "低分与错误案例行号": actualLowScoreRows,
    "首个评测结果差异": resultDifference,
    "原始主表保持不变": checks["原始主表保持不变"],
    "溯源明细保持不变": checks["溯源明细保持不变"],
    "公式错误数量": formulaErrorCount,
    "预览图数量": previewResult.rendered,
    "预览错误": previewResult.errors,
  };
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.writeFile(outputPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
  if (!passed) {
    const failedChecks = Object.entries(checks)
      .filter(([, value]) => !value)
      .map(([name]) => name)
      .join("、");
    throw new Error(`工作簿验证未通过：${failedChecks}`);
  }
}


function headerIndexes(headerRow) {
  return new Map(headerRow.map((value, index) => [normalizedText(value), index]));
}


function requiredHeader(indexes, name, aliases = []) {
  for (const candidate of [name, ...aliases]) {
    const index = indexes.get(candidate);
    if (index !== undefined) {
      return index;
    }
  }
  throw new Error(`溯源明细缺少表头：${name}`);
}


async function extract(options) {
  const inputPath = requiredOption(options, "input");
  const outputPath = requiredOption(options, "output");
  const input = await FileBlob.load(inputPath);
  const workbook = await SpreadsheetFile.importXlsx(input);
  const { name: mainSheetName, sheet: mainSheet } = requiredMainWorksheet(workbook);
  const detailSheet = requiredWorksheet(workbook, "溯源明细");
  const mainRows = mainSheet.getRange("A1:E51").values;
  const mainUsedRows = mainSheet.getUsedRange(true).values;
  const detailRows = detailSheet.getUsedRange().values;

  if (mainRows.length !== 51) {
    throw new Error(`主表必须包含 51 行，实际为 ${mainRows.length} 行`);
  }
  const extraRowOffset = mainUsedRows
    .slice(51)
    .findIndex((row) => row.slice(0, 5).some((value) => normalizedText(value) !== ""));
  if (extraRowOffset !== -1) {
    throw new Error(
      `主表 A:E 有效数据行必须恰好为 51 行（含表头），` +
      `检测到第 ${extraRowOffset + 52} 行仍有数据`,
    );
  }
  if (detailRows.length < 1) {
    throw new Error("溯源明细工作表没有表头");
  }

  const indexes = headerIndexes(detailRows[0]);
  const rowIndex = requiredHeader(indexes, "评测行号");
  const chunkIndex = requiredHeader(indexes, "chunk_id");
  const sourceIndex = requiredHeader(indexes, "来源路径", ["source_path"]);
  const locatorIndex = requiredHeader(indexes, "来源定位");
  const excerptIndex = requiredHeader(indexes, "原文摘录");
  const noteIndex = requiredHeader(indexes, "支撑说明", ["支撑范围及缺失说明"]);
  const detailsByRow = new Map();

  for (const row of detailRows.slice(1)) {
    if (row.every((value) => normalizedText(value) === "")) {
      continue;
    }
    const excelRow = Number(row[rowIndex]);
    if (!Number.isInteger(excelRow) || excelRow < 2 || excelRow > 51) {
      throw new Error(`溯源明细的评测行号无法关联主表：${row[rowIndex] ?? ""}`);
    }
    const detail = {
      chunkId: normalizedText(row[chunkIndex]),
      sourcePath: normalizedText(row[sourceIndex]),
      locator: normalizedText(row[locatorIndex]),
      excerpt: normalizedText(row[excerptIndex]),
      supportNote: normalizedText(row[noteIndex]),
    };
    const grouped = detailsByRow.get(excelRow) ?? [];
    grouped.push(detail);
    detailsByRow.set(excelRow, grouped);
  }

  const evaluationRows = mainRows.slice(1).map((row, index) => {
    const excelRow = index + 2;
    const question = normalizedText(row[0]);
    const referenceAnswer = normalizedText(row[1]);
    if (!question || !referenceAnswer) {
      throw new Error(`主表第 ${excelRow} 行的问题和参考答案不能为空`);
    }
    const details = detailsByRow.get(excelRow) ?? [];
    const traceStatus = normalizedText(row[2]);
    return {
      "评测项ID": `row-${excelRow}`,
      "Excel行号": excelRow,
      "问题": question,
      "参考答案": referenceAnswer,
      "溯源状态": traceStatus,
      "主chunk_id": traceStatus === "未定位" ? "" : normalizedText(row[3]),
      "黄金chunk_id列表": [
        ...new Set(details.map((item) => item.chunkId).filter(Boolean)),
      ],
      "黄金证据": details.map((item) => ({
        "chunk_id": item.chunkId,
        "来源路径": item.sourcePath,
        "来源定位": item.locator,
        "原文摘录": item.excerpt,
        "支撑说明": item.supportNote,
      })),
    };
  });

  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.writeFile(
    outputPath,
    JSON.stringify(
      {
        "评测项": evaluationRows,
        "工作簿快照": {
          "主表名称": mainSheetName,
          "主表A:E": mainRows,
          "溯源明细": detailRows,
        },
      },
      null,
      2,
    ),
    "utf8",
  );
}


async function main() {
  const mode = process.argv[2];
  const options = parseArguments(process.argv.slice(3));
  if (mode === "extract") {
    await extract(options);
    return;
  }
  if (mode === "backfill") {
    await backfill(options);
    return;
  }
  if (mode === "verify") {
    await verify(options);
    return;
  }
  throw new Error(`不支持的工作簿模式：${mode ?? ""}`);
}


main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`工作簿处理失败：${message}`);
  process.exitCode = 1;
});
