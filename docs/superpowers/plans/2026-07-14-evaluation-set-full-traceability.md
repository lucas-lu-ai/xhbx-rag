# Evaluation Set Full Traceability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 生成一份不覆盖原文件的评测集副本，为 50 组问答建立到全部 `parsed/**/chunks.jsonl`、chunk citation 和原始文件定位的完整溯源。

**Architecture:** 使用一个临时 Python 匹配器读取 2631 个 chunk，按问题、标准答案、chunk 正文和 citation 原文进行候选召回与逐观点覆盖判定，输出结构化 `traceability.json`。使用一个临时 `@oai/artifact-tool` JavaScript 构建器导入原工作簿、写入原表溯源摘要和“溯源明细”工作表、导出并渲染验证。

**Tech Stack:** bundled Python 3、Python 标准库、bundled Node.js、`@oai/artifact-tool` 2.8.6+、JSON/JSONL、XLSX。

## Global Constraints

- 输入工作簿固定为 `/Users/milan/xhbx-rag/docs/新华保险AI教练问答一批绩优案例测试集.xlsx`。
- 溯源语料固定为 `/Users/milan/xhbx-rag/parsed/**/chunks.jsonl`。
- 输出固定为 `/Users/milan/xhbx-rag/outputs/019f5f79-943c-7611-b046-f9725e18b5bd/新华保险AI教练问答一批绩优案例测试集-含完整溯源.xlsx`。
- 不覆盖输入工作簿，不修改原问答前两列的值和顺序。
- 所有 XLSX 读写必须使用 bundled `@oai/artifact-tool`；不得使用 openpyxl、xlsxwriter 或 pandas ExcelWriter。
- 溯源状态只表示源文支撑程度，不表示法律、监管或事实正确性。
- 不向工作簿新增客户姓名、电话、证件号、保单号等个人敏感信息。
- 临时脚本、JSON 和预览只保存在 `/tmp/xhbx_eval_trace_019f5f79/`，不提交到仓库。

---

### Task 1: 建立可测试的 chunk 匹配与定位格式化逻辑

**Files:**
- Create: `/tmp/xhbx_eval_trace_019f5f79/trace_matcher.py`
- Create: `/tmp/xhbx_eval_trace_019f5f79/test_trace_matcher.py`

**Interfaces:**
- Consumes: 问题文本、标准答案文本、`chunks.jsonl` 解析后的字典。
- Produces: `normalize(text) -> str`、`split_claims(answer) -> list[str]`、`score_chunk(question, answer, chunk) -> float`、`score_citation(claims, citation) -> tuple[float, list[int]]`、`format_locator(locator) -> str`。

- [ ] **Step 1: 编写失败测试**

```python
import unittest

from trace_matcher import format_locator, normalize, score_citation, split_claims


class TraceMatcherTests(unittest.TestCase):
    def test_normalize_and_split_claims(self):
        self.assertEqual(normalize("第一步：线上经营。"), "第一步线上经营")
        self.assertEqual(
            split_claims("第一步：线上经营。\n第二步：线下服务。"),
            ["第一步线上经营", "第二步线下服务"],
        )

    def test_score_citation_requires_direct_support(self):
        claims = split_claims("保险公司可以依法破产。寿险合同需要转让。")
        citation = {"quote": "保险公司可以依法破产，寿险合同及责任准备金必须转让。"}
        score, covered = score_citation(claims, citation)
        self.assertGreaterEqual(score, 0.55)
        self.assertEqual(covered, [0, 1])

    def test_format_locator_keeps_all_available_coordinates(self):
        locator = {"page": 3, "slide": 7, "line_start": 10, "line_end": 12,
                   "heading_path": ["第一章", "第一节"]}
        self.assertEqual(
            format_locator(locator),
            "页3；幻灯片7；行10-12；第一章 > 第一节",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
cd /tmp/xhbx_eval_trace_019f5f79
/Users/milan/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest -v test_trace_matcher.py
```

Expected: FAIL，错误为 `ModuleNotFoundError: No module named 'trace_matcher'`。

- [ ] **Step 3: 实现最小匹配函数**

```python
import re


def normalize(text):
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text or "").lower()


def split_claims(answer):
    parts = [normalize(part) for part in re.split(r"[\n。；;]+", answer or "")]
    return [part for part in parts if len(part) >= 4]


def _ngrams(text, n=2):
    value = normalize(text)
    return {value[i:i+n] for i in range(max(0, len(value) - n + 1))}


def _containment(left, right, n=2):
    left_grams = _ngrams(left, n)
    right_grams = _ngrams(right, n)
    return len(left_grams & right_grams) / len(left_grams) if left_grams else 0.0


def score_citation(claims, citation):
    evidence = "\n".join(str(citation.get(key, "")) for key in
                         ("quote", "source_excerpt", "context"))
    covered = []
    scores = []
    for index, claim in enumerate(claims):
        exact = len(claim) >= 6 and claim in normalize(evidence)
        score = 1.0 if exact else max(_containment(claim, evidence, 2),
                                      _containment(claim, evidence, 3))
        if score >= 0.42:
            covered.append(index)
            scores.append(score)
    return (sum(scores) / len(claims) if claims else 0.0, covered)


def score_chunk(question, answer, chunk):
    corpus = "\n".join([
        str(chunk.get("text", "")),
        str(chunk.get("chunk_id", "")),
        str(chunk.get("metadata", {}).get("case_name", "")),
        "\n".join(str(item.get("quote", "")) for item in chunk.get("citations", [])),
    ])
    return 0.25 * _containment(question, corpus, 2) + 0.75 * _containment(answer, corpus, 2)


def format_locator(locator):
    locator = locator or {}
    parts = []
    if locator.get("page") is not None:
        parts.append(f"页{locator['page']}")
    if locator.get("slide") is not None:
        parts.append(f"幻灯片{locator['slide']}")
    if locator.get("line_start") is not None:
        end = locator.get("line_end", locator["line_start"])
        parts.append(f"行{locator['line_start']}-{end}")
    if locator.get("heading_path"):
        parts.append(" > ".join(map(str, locator["heading_path"])))
    return "；".join(parts)
```

- [ ] **Step 4: 运行测试并确认通过**

Run the unittest command from Step 2.

Expected: `Ran 3 tests` and `OK`。

---

### Task 2: 提取评测集并生成完整 traceability.json

**Files:**
- Create: `/tmp/xhbx_eval_trace_019f5f79/build_workbook.mjs`
- Modify: `/tmp/xhbx_eval_trace_019f5f79/trace_matcher.py`
- Create: `/tmp/xhbx_eval_trace_019f5f79/qa_rows.json`
- Create: `/tmp/xhbx_eval_trace_019f5f79/traceability.json`

**Interfaces:**
- Consumes: 原始 XLSX、全部 `parsed/**/chunks.jsonl`。
- Produces: `qa_rows.json`，每条记录包含 `excel_row`、`question`、`answer`；`traceability.json` 包含每条问答的 `status`、`primary_chunk_id`、`effective_source_count` 和 `details`。

- [ ] **Step 1: 用同一 JavaScript 构建器实现 extract 模式**

```js
import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "/Users/milan/xhbx-rag/docs/新华保险AI教练问答一批绩优案例测试集.xlsx";
const tempDir = "/tmp/xhbx_eval_trace_019f5f79";
const mode = process.argv[2];

if (mode === "extract") {
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
  const sheet = workbook.worksheets.getItem("绩优案例测试-楚琦");
  const values = sheet.getRange("A1:B51").values;
  const rows = values.slice(1).map((row, index) => ({
    excel_row: index + 2,
    question: row[0],
    answer: row[1],
  }));
  await fs.writeFile(`${tempDir}/qa_rows.json`, JSON.stringify(rows, null, 2), "utf8");
}
```

- [ ] **Step 2: 运行 extract 并核对 50 行**

Run:

```bash
cd /tmp/xhbx_eval_trace_019f5f79
/Users/milan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node build_workbook.mjs extract
jq 'length' qa_rows.json
```

Expected: `50`。

- [ ] **Step 3: 在 Python 匹配器中读取 2631 个 chunk 并生成候选**

实现流程：

```python
for qa in qa_rows:
    ranked = sorted(chunks, key=lambda chunk: score_chunk(
        qa["question"], qa["answer"], chunk), reverse=True)[:20]
    claims = split_claims(qa["answer"])
    details = []
    covered_claims = set()
    for chunk in ranked:
        for citation in chunk.get("citations", []) or [{}]:
            support_score, covered = score_citation(claims, citation)
            if support_score < 0.12 or not covered:
                continue
            covered_claims.update(covered)
            details.append(build_detail(qa, chunk, citation, support_score, covered))
    details = dedupe_details(details)
    status = ("完整支持" if claims and len(covered_claims) == len(claims)
              else "部分支持" if covered_claims else "未定位")
```

`build_detail()` 必须输出设计规范中的全部字段；`dedupe_details()` 使用 `(chunk_id, source_path, locator_json)` 去重。

- [ ] **Step 4: 人工复核每条问答的候选与覆盖结论**

生成按 `excel_row` 排序的检查文本，每题展示问题、答案观点、候选 chunk、citation 摘录和覆盖编号。逐行检查 50 条：

- 删除只主题相似、不支撑具体答案的记录；
- 对自动评分漏掉但原文明确支撑的 citation 加入明细；
- 对第16、23、25、29、35、43、45、51行重点复核；
- 对标准答案超出源文的内容保留“部分支持”，在缺失说明中列出未支撑观点；
- 不因来源材料本身存在法律错误而改为“未定位”。

- [ ] **Step 5: 验证 traceability.json 的结构与引用真实性**

Run:

```bash
/Users/milan/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m json.tool traceability.json >/dev/null
jq 'length' traceability.json
jq '[.[].details | length] | add' traceability.json
```

Expected: 第一条命令退出码 0；第二条输出 `50`；第三条输出大于 0。

---

### Task 3: 构建带双工作表溯源的 XLSX

**Files:**
- Modify: `/tmp/xhbx_eval_trace_019f5f79/build_workbook.mjs`
- Create: `/Users/milan/xhbx-rag/outputs/019f5f79-943c-7611-b046-f9725e18b5bd/新华保险AI教练问答一批绩优案例测试集-含完整溯源.xlsx`

**Interfaces:**
- Consumes: 原始 XLSX、`traceability.json`。
- Produces: 已格式化并可筛选的最终 XLSX。

- [ ] **Step 1: 在 JavaScript 构建器中实现 build 模式**

```js
const traceability = JSON.parse(await fs.readFile(`${tempDir}/traceability.json`, "utf8"));
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const qaSheet = workbook.worksheets.getItem("绩优案例测试-楚琦");

qaSheet.getRange("C1:E1").values = [["溯源状态", "主 chunk_id", "有效溯源数"]];
qaSheet.getRange("C2:E51").values = traceability.map((item) => [
  item.status,
  item.primary_chunk_id,
  item.effective_source_count,
]);
qaSheet.freezePanes.freezeRows(1);

const detailSheet = workbook.worksheets.getOrAdd("溯源明细");
const headers = [["评测行号", "问题", "匹配顺序", "chunk_id", "chunk_type", "案例名称",
  "source_file", "source_path", "section_name", "来源定位", "原文摘录",
  "定位置信度", "支撑范围及缺失说明"]];
const detailRows = traceability.flatMap((item) => item.details.map((detail, index) => [
  item.excel_row, item.question, index + 1, detail.chunk_id, detail.chunk_type,
  detail.case_name, detail.source_file, detail.source_path, detail.section_name,
  detail.locator_text, detail.source_excerpt, detail.locator_confidence,
  detail.support_note,
]));
detailSheet.getRangeByIndexes(0, 0, 1, headers[0].length).values = headers;
detailSheet.getRangeByIndexes(1, 0, detailRows.length, headers[0].length).values = detailRows;
detailSheet.freezePanes.freezeRows(1);
```

继续在 build 模式中：

- 复制原表表头的字体、填充、边框和对齐方式到 C1:E1；
- 为 C:E 设置换行和合理列宽；
- 为 C2:C51 添加绿色、黄色、红色条件格式；
- 若原表已有仅覆盖 A:B 的筛选表格，先记录其名称和样式，删除后重建为覆盖 A:E 的单一表格；明细表只创建一个覆盖实际使用范围的表格，确保没有重叠表格；
- 为明细表设置深色表头、浅色结构边框、自动换行、顶部对齐和合理列宽；
- 只对实际使用范围应用格式。

- [ ] **Step 2: 导出到指定输出目录**

```js
await fs.mkdir(outputDir, { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(`${outputDir}/新华保险AI教练问答一批绩优案例测试集-含完整溯源.xlsx`);
```

- [ ] **Step 3: 检查关键范围和公式错误**

在同一构建器中调用：

```js
console.log((await workbook.inspect({
  kind: "table",
  range: "绩优案例测试-楚琦!A1:E51",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 5,
})).ndjson);
console.log((await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
})).ndjson);
```

Expected: 原表出现 A:E 五列；错误扫描匹配 0 项。

---

### Task 4: 完整性、可追溯性和视觉验收

**Files:**
- Modify: `/tmp/xhbx_eval_trace_019f5f79/build_workbook.mjs`
- Create: `/tmp/xhbx_eval_trace_019f5f79/qa_sheet.png`
- Create: `/tmp/xhbx_eval_trace_019f5f79/trace_detail.png`

**Interfaces:**
- Consumes: 最终 XLSX。
- Produces: 结构验证结果、两个工作表的视觉预览和最终验收结论。

- [ ] **Step 1: 重新导入最终 XLSX 并验证不变量**

验证：

- 工作表名称为 `绩优案例测试-楚琦` 和 `溯源明细`；
- 原表 A2:B51 与输入文件逐单元格完全一致；
- C2:C51 无空值；
- E2:E51 等于明细表按评测行号分组后的 distinct chunk 数量；
- 所有非空主 `chunk_id` 均存在于 2631 个源 chunk ID 集合中；
- 每条明细的 `chunk_id`、`source_path` 和定位文本能够回查到对应 JSONL 记录。

- [ ] **Step 2: 渲染两个工作表**

```js
const qaPreview = await workbook.render({
  sheetName: "绩优案例测试-楚琦", autoCrop: "all", scale: 1, format: "png",
});
const detailPreview = await workbook.render({
  sheetName: "溯源明细", autoCrop: "all", scale: 1, format: "png",
});
```

保存为 `qa_sheet.png` 和 `trace_detail.png`，逐张查看。

- [ ] **Step 3: 修复严重视觉问题并重新验证**

只修复明确问题：表头或关键文本截断、列宽失衡、深度换行导致不可读、条件格式失效、空白默认工作表、内容超出可见区域。修改后重新导出、重新导入并重复 Steps 1–2。

- [ ] **Step 4: 最终交付检查**

Run:

```bash
ls -lh '/Users/milan/xhbx-rag/outputs/019f5f79-943c-7611-b046-f9725e18b5bd/新华保险AI教练问答一批绩优案例测试集-含完整溯源.xlsx'
git status --short
```

Expected: XLSX 文件存在且非空；仓库只包含已确认的设计和计划文档提交，不包含临时脚本或输出文件的意外变更。
