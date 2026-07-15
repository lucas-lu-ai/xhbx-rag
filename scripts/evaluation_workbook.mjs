import fs from "node:fs/promises";
import path from "node:path";

import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";


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


function normalizedText(value) {
  return String(value ?? "").trim();
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
  const mainSheet = requiredWorksheet(workbook, "绩优案例测试-楚琦");
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
    return {
      "评测项ID": `row-${excelRow}`,
      "Excel行号": excelRow,
      "问题": question,
      "参考答案": referenceAnswer,
      "溯源状态": normalizedText(row[2]),
      "主chunk_id": normalizedText(row[3]),
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
    JSON.stringify({ "评测项": evaluationRows }, null, 2),
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
  if (mode === "backfill" || mode === "verify") {
    throw new Error(`工作簿模式尚未实现：${mode}`);
  }
  throw new Error(`不支持的工作簿模式：${mode ?? ""}`);
}


main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`工作簿处理失败：${message}`);
  process.exitCode = 1;
});
