# 问答智能体评测智能体实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一个可重复执行的 CLI 评测智能体，直接调用本地问答代码和 Docker Milvus 跑完 50 条评测集，使用混合评分生成全中文结果，并安全回填到 `docs/新华保险AI教练问答一批绩优案例测试集-含完整溯源.xlsx`。

**Architecture:** Python 评测核心负责中文数据契约、Docker Milvus 预检、本地问答、裁判模型、确定性指标、断点续跑和 Markdown/JSON 报告；独立 JavaScript 工作簿适配器使用 `@oai/artifact-tool` 读取和回填现有 XLSX。每题终态先写入中文 JSONL，工作簿先备份并生成临时文件，结构与视觉检查通过后再原子替换目标文件。

**Tech Stack:** Python 3.12、Pydantic 2、httpx、pymilvus、标准库 `concurrent.futures` / `json` / `hashlib`、pytest、bundled Node.js、`@oai/artifact-tool` 2.8.6+、Docker Compose、Milvus 2.6.19。

## Global Constraints

- 唯一输入和最终回填目标：`/Users/milan/xhbx-rag/docs/新华保险AI教练问答一批绩优案例测试集-含完整溯源.xlsx`。
- 保持 `绩优案例测试-楚琦!A:E` 和整个 `溯源明细` 工作表的值、顺序与既有样式不变。
- 从 F 列起回填中文评测字段；新增或幂等重建 `评测总览`、`低分与错误案例`、`运行元数据`。
- 50 条全部进入主结果；总体保守通过率分母固定为 50。
- 问答核心直接调用 `xhbx_rag.web.services.answer_question()`；不启动 `api`、`web` 或 A2A 服务。
- 只允许 `MILVUS_MODE=docker`；宿主机连接 `http://localhost:19530`；不打开 Milvus Lite。
- 默认问答并发 2、裁判并发 2、`top_n=20`、`top_k=5`。
- 评分固定为：事实正确性 35、关键点覆盖 20、证据忠实性 20、引用及黄金来源命中 15、相关性与表达 10；75 分合格，85 分优秀。
- 未配置 `EVAL_BASE_URL`、`EVAL_API_KEY`、`EVAL_MODEL_NAME` 时回退现有问答模型，并在所有报告中写明 `同模型裁判：是`。
- Excel、Markdown、JSON、JSONL 的对外业务字段、等级、状态、错误标签、扣分原因和建议全部使用中文。
- XLSX 读写只使用 bundled `@oai/artifact-tool`；不得使用 openpyxl、xlsxwriter、pandas ExcelWriter 或 repo/global Node 依赖。
- 当前 bundled Node：`/Users/milan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`。
- 当前 bundled node_modules：`/Users/milan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules`。
- 回填前把源文件逐字节复制为 `outputs/evaluations/<run_id>/input-backup.xlsx`；临时工作簿验证通过后才原子替换源文件。
- 不自动清空、重建或增量写入 Docker Milvus；索引为空时退出码为 2。
- 实现遵循 TDD；每个任务先看到相关测试失败，再写最小实现并提交。

---

## 文件结构

- `src/xhbx_rag/evaluation/__init__.py`：公开评测入口与版本常量。
- `src/xhbx_rag/evaluation/models.py`：评测集、裁判、规则分、逐题结果和汇总的 Pydantic 契约与中文别名。
- `src/xhbx_rag/evaluation/config.py`：裁判模型配置、运行参数和同模型回退判断。
- `src/xhbx_rag/evaluation/serialization.py`：中文 JSON/JSONL 序列化和英文业务字段阻断。
- `src/xhbx_rag/evaluation/dataset.py`：规范化数据加载、黄金证据聚合与 chunk 目录索引。
- `src/xhbx_rag/evaluation/workbook.py`：复制工作簿脚本到临时目录、链接 bundled node_modules、执行抽取/回填/验证。
- `src/xhbx_rag/evaluation/metrics.py`：15 分确定性指标、100 分聚合、等级和总体汇总。
- `src/xhbx_rag/evaluation/judge.py`：独立裁判模型调用、结构化输出校验与中文修复重试。
- `src/xhbx_rag/evaluation/runner.py`：Docker Milvus 预检、本地问答重试、并发、检查点和断点续跑。
- `src/xhbx_rag/evaluation/reporting.py`：中文 `run.json`、`results.jsonl`、`report.md` 和回填 payload。
- `src/xhbx_rag/evaluation/command.py`：CLI 编排、退出码、备份、临时文件验证与原子替换。
- `scripts/evaluation_workbook.mjs`：`extract`、`backfill`、`verify` 三种工作簿模式。
- `src/xhbx_rag/cli.py`：注册 `evaluate` 子命令。
- `README.md`：Docker Milvus 本地评测、中文输出和安全回填说明。
- `tests/test_evaluation_*.py`：各模块单元与工作簿集成测试。

---

### Task 1: 中文数据契约与裁判配置

**Files:**
- Create: `src/xhbx_rag/evaluation/__init__.py`
- Create: `src/xhbx_rag/evaluation/models.py`
- Create: `src/xhbx_rag/evaluation/config.py`
- Create: `src/xhbx_rag/evaluation/serialization.py`
- Test: `tests/test_evaluation_models.py`

**Interfaces:**
- Consumes: `.env` 或显式 `Mapping[str, str]`。
- Produces: `EvaluationItem`、`GoldEvidence`、`JudgeResult`、`DeterministicScores`、`EvaluationResult`、`EvaluationConfig`、`dump_chinese()`。

- [ ] **Step 1: 编写中文别名、分数边界和同模型回退的失败测试**

```python
import pytest
from xhbx_rag.evaluation.config import load_evaluation_config
from xhbx_rag.evaluation.models import JudgeResult
from xhbx_rag.evaluation.serialization import dump_chinese


def test_judge_result_serializes_only_chinese_business_fields():
    result = JudgeResult(
        correctness_score=30,
        keypoint_coverage_score=18,
        groundedness_score=17,
        relevance_clarity_score=9,
        reference_keypoints=["先确认客户预算"],
        covered_keypoints=["先确认客户预算"],
        missing_keypoints=[],
        unsupported_claims=[],
        error_tags=[],
        reason="回答与参考答案一致，且有证据支持。",
        improvement_suggestion="可以补充后续行动步骤。",
    )
    payload = dump_chinese(result)
    assert payload["事实正确性得分"] == 30
    assert payload["扣分原因"] == "回答与参考答案一致，且有证据支持。"
    assert "correctness_score" not in payload


def test_judge_result_rejects_out_of_range_score():
    with pytest.raises(ValueError):
        JudgeResult(
            correctness_score=36, keypoint_coverage_score=20,
            groundedness_score=20, relevance_clarity_score=10,
            reference_keypoints=[], covered_keypoints=[], missing_keypoints=[],
            unsupported_claims=[], error_tags=[], reason="分数越界。",
            improvement_suggestion="重新评分。",
        )


def test_evaluation_config_falls_back_to_same_model_and_marks_it():
    config = load_evaluation_config({
        "BASE_URL": "https://example.com/v1",
        "API_KEY": "answer-key",
        "MODEL_NAME": "answer-model",
    })
    assert config.judge_model_name == "answer-model"
    assert config.same_model_judge is True
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_evaluation_models.py -v`

Expected: FAIL，错误为 `ModuleNotFoundError: No module named 'xhbx_rag.evaluation'`。

- [ ] **Step 3: 实现 Pydantic 契约、中文别名和配置回退**

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

ERROR_TAGS = (
    "事实错误", "关键点缺失", "无依据扩写", "答非所问",
    "引用缺失", "检索未命中", "问答执行失败", "裁判执行失败",
)
EvaluationGrade = Literal["优秀", "合格", "不合格", "问答失败", "评测失败"]


class ChineseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class GoldEvidence(ChineseModel):
    chunk_id: str = Field(alias="chunk_id")
    source_path: str = Field(default="", alias="来源路径")
    locator: str = Field(default="", alias="来源定位")
    excerpt: str = Field(default="", alias="原文摘录")
    support_note: str = Field(default="", alias="支撑说明")


class EvaluationItem(ChineseModel):
    item_id: str = Field(alias="评测项ID")
    excel_row: int = Field(alias="Excel行号", ge=2)
    question: str = Field(alias="问题", min_length=1)
    reference_answer: str = Field(alias="参考答案", min_length=1)
    trace_status: Literal["完整支持", "部分支持", "未定位"] = Field(alias="溯源状态")
    primary_chunk_id: str = Field(default="", alias="主chunk_id")
    gold_chunk_ids: list[str] = Field(default_factory=list, alias="黄金chunk_id列表")
    gold_evidences: list[GoldEvidence] = Field(default_factory=list, alias="黄金证据")


class JudgeResult(ChineseModel):
    correctness_score: float = Field(alias="事实正确性得分", ge=0, le=35)
    keypoint_coverage_score: float = Field(alias="关键点覆盖得分", ge=0, le=20)
    groundedness_score: float = Field(alias="证据忠实性得分", ge=0, le=20)
    relevance_clarity_score: float = Field(alias="相关性与表达得分", ge=0, le=10)
    reference_keypoints: list[str] = Field(alias="参考答案关键点")
    covered_keypoints: list[str] = Field(alias="已覆盖关键点")
    missing_keypoints: list[str] = Field(alias="缺失关键点")
    unsupported_claims: list[str] = Field(alias="无依据表述")
    error_tags: list[str] = Field(alias="错误标签")
    reason: str = Field(alias="扣分原因", min_length=1)
    improvement_suggestion: str = Field(alias="改进建议", min_length=1)

    @field_validator("error_tags")
    @classmethod
    def validate_error_tags(cls, values):
        invalid = [value for value in values if value not in ERROR_TAGS]
        if invalid:
            raise ValueError(f"不支持的错误标签: {', '.join(invalid)}")
        return values


class DeterministicScores(ChineseModel):
    retrieval_score: float = Field(alias="检索规则得分", ge=0, le=10)
    citation_score: float = Field(alias="引用规则得分", ge=0, le=5)
    total: float = Field(alias="引用及黄金来源命中得分", ge=0, le=15)
    rule_name: str = Field(alias="规则名称")
    primary_chunk_hit: bool = Field(alias="主chunk命中")
    gold_chunk_recall: float = Field(alias="黄金chunk召回率", ge=0, le=1)
    retrieved_chunk_ids: list[str] = Field(alias="检索chunk_id列表")


class EvaluationResult(ChineseModel):
    item_id: str = Field(alias="评测项ID")
    excel_row: int = Field(alias="Excel行号")
    question: str = Field(alias="问题")
    reference_answer: str = Field(alias="参考答案")
    trace_status: str = Field(alias="溯源状态")
    answer: str = Field(default="", alias="智能体回答")
    answer_response: dict = Field(default_factory=dict, alias="问答原始结果")
    duration_seconds: float = Field(default=0, alias="耗时（秒）", ge=0)
    deterministic_scores: DeterministicScores | None = Field(default=None, alias="确定性指标")
    judge_result: JudgeResult | None = Field(default=None, alias="裁判结果")
    total_score: float | None = Field(default=None, alias="总分", ge=0, le=100)
    grade: EvaluationGrade = Field(alias="评测等级")
    status: Literal["已完成", "问答失败", "评测失败"] = Field(alias="评测状态")
    error_tags: list[str] = Field(default_factory=list, alias="错误标签")
    error_summary: str = Field(default="", alias="错误摘要")
```

`config.py` 定义不可变 `EvaluationConfig`，`load_evaluation_config()` 按 `EVAL_* → BASE_*` 回退，并校验 URL、模型名、正超时和重试次数。

`serialization.py` 先调用 `model_dump(mode="json", by_alias=True)`，再递归映射问答原始结果中的键：`original_query→原始问题`、`rewritten_query→改写问题`、`intent→意图`、`filters→过滤条件`、`answer→智能体回答`、`reasoning→思考过程`、`citations→引用`、`evidence_count→证据数量`、`retrieval_evidences→检索证据`、`chunk_type→chunk类型`、`text→证据正文`、`metadata→元数据`、`score→检索得分`、`evidence_index→证据序号`、`source_path→来源路径`、`locator→来源定位`、`source_excerpt→原文摘录`、`quote→引用原文`、`display_location→展示定位`、`display_excerpt→展示摘录`、`can_reveal→可查看源文件`、`selected→模型选中`；`chunk_id` 保持技术名称。完成映射后递归扫描禁用键 `correctness_score`、`passed`、`failed`、`unsupported_claims`，发现后抛出 `ValueError("对外结果包含英文业务字段")`。

- [ ] **Step 4: 运行测试并确认通过**

Run: `uv run pytest tests/test_evaluation_models.py -v`

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/xhbx_rag/evaluation tests/test_evaluation_models.py
git commit -m "feat: add chinese evaluation contracts"
```

---

### Task 2: 工作簿抽取适配器与评测集加载

**Files:**
- Create: `scripts/evaluation_workbook.mjs`
- Create: `src/xhbx_rag/evaluation/workbook.py`
- Create: `src/xhbx_rag/evaluation/dataset.py`
- Test: `tests/test_evaluation_dataset.py`

**Interfaces:**
- Consumes: 带 `绩优案例测试-楚琦` 和 `溯源明细` 的 XLSX；bundled Node 路径与 node_modules 路径。
- Produces: `WorkbookAdapter.extract()`、`load_dataset() -> list[EvaluationItem]`、`load_chunk_catalog() -> set[str]`。

- [ ] **Step 1: 编写规范化数据加载和重复题号的失败测试**

```python
import json
import pytest
from xhbx_rag.evaluation.dataset import load_dataset


def test_load_dataset_accepts_chinese_contract(tmp_path):
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps({"评测项": [{
        "评测项ID": "row-2", "Excel行号": 2, "问题": "如何沟通预算？",
        "参考答案": "先确认预算。", "溯源状态": "完整支持",
        "主chunk_id": "chunk-1", "黄金chunk_id列表": ["chunk-1"],
        "黄金证据": [{"chunk_id": "chunk-1", "原文摘录": "先确认预算"}],
    }] * 50}, ensure_ascii=False), encoding="utf-8")
    payload = json.loads(path.read_text(encoding="utf-8"))
    for index, row in enumerate(payload["评测项"], start=2):
        row["评测项ID"] = f"row-{index}"
        row["Excel行号"] = index
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    items = load_dataset(path)
    assert len(items) == 50
    assert items[0].gold_chunk_ids == ["chunk-1"]


def test_load_dataset_rejects_duplicate_excel_rows(tmp_path):
    rows = [{"评测项ID": f"row-{index}", "Excel行号": 2, "问题": "问题",
             "参考答案": "答案", "溯源状态": "未定位", "主chunk_id": "",
             "黄金chunk_id列表": [], "黄金证据": []} for index in range(50)]
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps({"评测项": rows}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="Excel行号重复"):
        load_dataset(path)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_evaluation_dataset.py -v`

Expected: FAIL，错误为缺少 `evaluation.dataset`。

- [ ] **Step 3: 实现 Python 数据加载与 chunk 目录**

```python
def load_dataset(path: Path) -> list[EvaluationItem]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("评测项")
    if not isinstance(rows, list) or len(rows) != 50:
        raise ValueError("评测集必须包含 50 条评测项")
    items = [EvaluationItem.model_validate(row) for row in rows]
    excel_rows = [item.excel_row for item in items]
    if len(excel_rows) != len(set(excel_rows)):
        raise ValueError("Excel行号重复")
    return sorted(items, key=lambda item: item.excel_row)


def load_chunk_catalog(parsed_root: Path) -> set[str]:
    chunk_ids = set()
    for path in sorted(parsed_root.glob("**/chunks.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                chunk_id = str(json.loads(line).get("chunk_id", "")).strip()
                if chunk_id:
                    chunk_ids.add(chunk_id)
    return chunk_ids
```

`WorkbookAdapter` 创建运行目录下 `.workbook_adapter/`，复制 `scripts/evaluation_workbook.mjs`，创建 `node_modules` symlink 指向 `EVALUATION_ARTIFACT_NODE_MODULES`，再用 `EVALUATION_NODE_BIN` 启动复制后的脚本。缺少路径时抛出中文配置错误，不搜索全局或 repo 依赖。`workbook.py` 同时提供模块入口，支持 `extract --input --output`、`backfill --input --payload --output`、`verify --input --snapshot --output --preview-dir`，所有模式都委托同一个 `WorkbookAdapter`。

- [ ] **Step 4: 实现 `extract` 模式**

`scripts/evaluation_workbook.mjs` 使用 `FileBlob.load()`、`SpreadsheetFile.importXlsx()`，读取 `绩优案例测试-楚琦!A1:E51` 和 `溯源明细` used range。按中文表头建立列索引，把溯源明细按 `评测行号` 聚合，输出中文 JSON：

```js
const payload = {
  "评测项": mainRows.slice(1).map((row, index) => ({
    "评测项ID": `row-${index + 2}`,
    "Excel行号": index + 2,
    "问题": String(row[0] ?? ""),
    "参考答案": String(row[1] ?? ""),
    "溯源状态": String(row[2] ?? ""),
    "主chunk_id": String(row[3] ?? ""),
    "黄金chunk_id列表": [...new Set((detailsByRow.get(index + 2) ?? []).map(item => item.chunkId))],
    "黄金证据": (detailsByRow.get(index + 2) ?? []).map(item => ({
      "chunk_id": item.chunkId, "来源路径": item.sourcePath,
      "来源定位": item.locator, "原文摘录": item.excerpt,
      "支撑说明": item.supportNote,
    })),
  })),
};
await fs.writeFile(outputPath, JSON.stringify(payload, null, 2), "utf8");
```

抽取器校验两个工作表存在、主表为 51 行、A/B 非空、评测行号能关联主表；错误信息使用中文。

- [ ] **Step 5: 运行测试并确认通过**

Run: `uv run pytest tests/test_evaluation_dataset.py -v`

Expected: 全部 PASS。

- [ ] **Step 6: 对真实工作簿只读抽取并核对 50 条**

```bash
export EVALUATION_NODE_BIN=/Users/milan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
export EVALUATION_ARTIFACT_NODE_MODULES=/Users/milan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules
uv run python -m xhbx_rag.evaluation.workbook extract --input "docs/新华保险AI教练问答一批绩优案例测试集-含完整溯源.xlsx" --output /tmp/xhbx-evaluation-dataset.json
```

Expected: 退出码 0；`评测项` 长度 50；状态分布 38/11/1；输入 XLSX 的 SHA-256 不变。

- [ ] **Step 7: 提交**

```bash
git add scripts/evaluation_workbook.mjs src/xhbx_rag/evaluation/workbook.py src/xhbx_rag/evaluation/dataset.py tests/test_evaluation_dataset.py
git commit -m "feat: extract traced evaluation dataset"
```

---

### Task 3: 确定性指标、总分和汇总

**Files:**
- Create: `src/xhbx_rag/evaluation/metrics.py`
- Modify: `src/xhbx_rag/evaluation/models.py`
- Test: `tests/test_evaluation_metrics.py`

**Interfaces:**
- Consumes: `EvaluationItem`、问答响应字典、chunk 目录、`JudgeResult`。
- Produces: `score_deterministic()`、`aggregate_result()`、`summarize_results()`。

- [ ] **Step 1: 编写黄金命中、有效引用、未定位替代规则和等级边界的失败测试**

```python
def test_deterministic_score_for_traced_item():
    item = make_item(primary="c1", gold=["c1", "c2"], status="完整支持")
    response = {
        "retrieval_evidences": [{"chunk_id": "c1"}, {"chunk_id": "c3"}],
        "citations": [{"evidence_index": 1, "source_path": "case/a.txt", "locator": {"line_start": 2}}],
    }
    score = score_deterministic(item, response, {"c1", "c2", "c3"})
    assert score.primary_chunk_hit is True
    assert score.gold_chunk_recall == 0.5
    assert score.retrieval_score == 7.5
    assert score.citation_score == 5.0
    assert score.total == 12.5


def test_unlocated_item_uses_catalog_validity_not_gold_hit():
    item = make_item(primary="", gold=[], status="未定位")
    response = {"retrieval_evidences": [{"chunk_id": "known"}], "citations": []}
    score = score_deterministic(item, response, {"known"})
    assert score.rule_name == "检索证据有效性"
    assert score.retrieval_score == 10.0
    assert score.citation_score == 0.0


@pytest.mark.parametrize(("total", "grade"), [
    (85, "优秀"), (84.99, "合格"), (75, "合格"), (74.99, "不合格"),
])
def test_grade_boundaries(total, grade):
    assert grade_for_score(total) == grade
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_evaluation_metrics.py -v`

Expected: FAIL，错误为缺少 `evaluation.metrics`。

- [ ] **Step 3: 实现固定公式**

```python
def score_deterministic(item, response, chunk_catalog):
    evidences = [row for row in response.get("retrieval_evidences", []) if isinstance(row, dict)]
    retrieved_by_index = [str(row.get("chunk_id", "")).strip() for row in evidences]
    retrieved = [value for value in retrieved_by_index if value]
    if item.trace_status == "未定位":
        non_empty = 5.0 if retrieved else 0.0
        valid_count = sum(chunk_id in chunk_catalog for chunk_id in retrieved)
        catalog_ratio = valid_count / len(retrieved) if retrieved else 0.0
        retrieval_score = non_empty + 5.0 * catalog_ratio
        rule_name = "检索证据有效性"
        main_hit = False
        recall = 0.0
    else:
        main_hit = bool(item.primary_chunk_id and item.primary_chunk_id in retrieved)
        gold_ids = set(item.gold_chunk_ids)
        recall = len(set(retrieved) & gold_ids) / len(gold_ids) if gold_ids else 0.0
        retrieval_score = 5.0 * float(main_hit) + 5.0 * recall
        rule_name = "黄金来源命中"
    citations = [row for row in response.get("citations", []) if isinstance(row, dict)]
    mapped = [int(row["evidence_index"]) for row in citations if str(row.get("evidence_index", "")).isdigit()]
    valid = [index for index in mapped if 1 <= index <= len(evidences)]
    valid_ratio = len(valid) / len(citations) if citations else 0.0
    locatable_ratio = sum(bool(row.get("source_path") and row.get("locator")) for row in citations) / len(citations) if citations else 0.0
    if item.trace_status == "未定位":
        citation_score = 2.5 * valid_ratio + 2.5 * locatable_ratio
    else:
        cited_ids = {retrieved_by_index[index - 1] for index in valid if retrieved_by_index[index - 1]}
        citation_score = 2.5 * valid_ratio + 2.5 * float(bool(cited_ids & set(item.gold_chunk_ids)))
    return DeterministicScores(
        retrieval_score=round(retrieval_score, 2), citation_score=round(citation_score, 2),
        total=round(retrieval_score + citation_score, 2), rule_name=rule_name,
        primary_chunk_hit=main_hit, gold_chunk_recall=recall,
        retrieved_chunk_ids=retrieved,
    )
```

`aggregate_result()` 将裁判四项加规则 15 分，四舍五入到 2 位；问答失败固定 0 分、等级 `问答失败`；裁判失败总分 `None`、等级 `评测失败`。`summarize_results()` 输出总题数 50、保守通过率、有效通过率、优秀率、问答成功率、均分、P50/P95、证据指标和 38/11/1 分层统计。

- [ ] **Step 4: 运行测试并确认通过**

Run: `uv run pytest tests/test_evaluation_metrics.py -v`

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/xhbx_rag/evaluation/models.py src/xhbx_rag/evaluation/metrics.py tests/test_evaluation_metrics.py
git commit -m "feat: score qa evaluation results"
```

---

### Task 4: 独立裁判智能体与中文修复重试

**Files:**
- Create: `src/xhbx_rag/evaluation/judge.py`
- Test: `tests/test_evaluation_judge.py`

**Interfaces:**
- Consumes: `EvaluationConfig`、`EvaluationItem`、本地问答响应。
- Produces: `EvaluationJudgeAgent.evaluate() -> JudgeResult`、`JudgeEvaluationError`。

- [ ] **Step 1: 编写请求体、结构化解析和中文修复重试的失败测试**

```python
def test_judge_sends_temperature_zero_and_returns_chinese_result():
    client = FakeClient([valid_judge_response()])
    agent = EvaluationJudgeAgent(make_config(), http_client=client)
    result = agent.evaluate(make_item(), make_answer())
    assert client.requests[0]["json"]["temperature"] == 0
    assert client.requests[0]["json"]["response_format"] == {"type": "json_object"}
    assert result.reason == "回答覆盖主要观点，引用证据基本充分。"


def test_judge_retries_invalid_json_with_chinese_repair_prompt():
    client = FakeClient([response_with_content("not-json"), valid_judge_response()])
    agent = EvaluationJudgeAgent(make_config(), http_client=client)
    result = agent.evaluate(make_item(), make_answer())
    assert result.correctness_score == 30
    assert "上一次输出无法通过评测结构校验" in client.requests[1]["json"]["messages"][-1]["content"]


def test_judge_rejects_english_reason_after_retries():
    client = FakeClient([english_judge_response()] * 3)
    agent = EvaluationJudgeAgent(make_config(), http_client=client)
    with pytest.raises(JudgeEvaluationError, match="裁判输出未使用中文"):
        agent.evaluate(make_item(), make_answer())
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_evaluation_judge.py -v`

Expected: FAIL，错误为缺少 `evaluation.judge`。

- [ ] **Step 3: 实现裁判提示词、请求和三次总尝试**

系统提示词必须包含：只依据问题、参考答案、黄金证据、智能体答案和检索证据评分；参考答案的未溯源扩写不是自动真理；不得因措辞不同扣事实分；四项分值上限；错误标签固定中文枚举；解释必须使用简体中文；只输出 JSON object。

```python
class EvaluationJudgeAgent:
    def __init__(self, config, http_client=None):
        self.config = config
        self.http_client = http_client or httpx.Client(timeout=config.judge_timeout)
        self._owns_client = http_client is None

    def evaluate(self, item, answer_response):
        messages = build_judge_messages(item, answer_response)
        last_error = None
        for attempt in range(self.config.judge_retry_attempts + 1):
            response = self.http_client.post(
                f"{self.config.judge_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.config.judge_api_key}"},
                json={"model": self.config.judge_model_name, "messages": messages,
                      "temperature": 0, "response_format": {"type": "json_object"}},
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            try:
                result = JudgeResult.model_validate_json(strip_json_fences(content))
                require_chinese_explanation(result.reason, result.improvement_suggestion)
                return result
            except (ValueError, ValidationError, json.JSONDecodeError) as exc:
                last_error = exc
                messages = repair_messages(messages, content, exc)
        raise JudgeEvaluationError(safe_judge_error(last_error))
```

`require_chinese_explanation()` 要求 `扣分原因` 和 `改进建议` 各自至少包含一个 `\u4e00-\u9fff` 字符；允许 `chunk_id`、模型名和缩写保留原值。异常不得包含 API key 或完整无界模型输出。

- [ ] **Step 4: 运行测试并确认通过**

Run: `uv run pytest tests/test_evaluation_judge.py -v`

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/xhbx_rag/evaluation/judge.py tests/test_evaluation_judge.py
git commit -m "feat: add chinese evaluation judge agent"
```

---

### Task 5: Docker Milvus 预检、本地问答运行器与断点续跑

**Files:**
- Create: `src/xhbx_rag/evaluation/runner.py`
- Modify: `src/xhbx_rag/evaluation/models.py`
- Test: `tests/test_evaluation_runner.py`

**Interfaces:**
- Consumes: `EvaluationItem` 列表、`RetrievalConfig`、`EvaluationConfig`、`answer_question`、`EvaluationJudgeAgent`。
- Produces: `preflight_docker_milvus()`、`compute_run_fingerprint()`、`run_items()`、中文 JSONL 检查点。

- [ ] **Step 1: 编写 Docker-only、空索引、重试、失败计 0 和 resume 指纹测试**

```python
def test_preflight_rejects_lite_mode():
    with pytest.raises(EvaluationPreflightError, match="只允许使用 Docker Milvus"):
        preflight_docker_milvus(make_retrieval_config(milvus_mode="lite"), client_factory=FakeMilvusClient)


def test_preflight_rejects_all_empty_collections():
    config = make_retrieval_config(milvus_mode="docker")
    factory = lambda **kwargs: FakeMilvusClient({"case": 0, "course": 0})
    with pytest.raises(EvaluationPreflightError, match="目标 collection 均为空"):
        preflight_docker_milvus(config, client_factory=factory)


def test_answer_transient_failure_retries_then_succeeds():
    answer = SequenceAnswer([httpx.ReadTimeout("timeout"), make_answer_response()])
    result = run_one_item(make_item(), answer_fn=answer, judge=FakeJudge(), max_attempts=3)
    assert answer.calls == 2
    assert result.grade in {"优秀", "合格", "不合格"}


def test_answer_final_failure_is_zero_and_chinese():
    answer = SequenceAnswer([httpx.ReadTimeout("timeout")] * 3)
    result = run_one_item(make_item(), answer_fn=answer, judge=FakeJudge(), max_attempts=3)
    assert result.total_score == 0
    assert result.grade == "问答失败"
    assert result.error_tags == ["问答执行失败"]


def test_resume_rejects_mismatched_fingerprint(tmp_path):
    write_run_metadata(tmp_path, fingerprint="old")
    with pytest.raises(ValueError, match="运行配置指纹不一致"):
        validate_resume(tmp_path, expected_fingerprint="new")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_evaluation_runner.py -v`

Expected: FAIL，错误为缺少 `evaluation.runner`。

- [ ] **Step 3: 实现 Docker Milvus 预检**

```python
def preflight_docker_milvus(config, client_factory=MilvusClient):
    if config.milvus_mode != "docker":
        raise EvaluationPreflightError("评测只允许使用 Docker Milvus")
    client = client_factory(uri=config.milvus_uri, token=config.milvus_token)
    stats = {}
    try:
        for name in configured_collection_names(config):
            exists = bool(client.has_collection(collection_name=name))
            count = int(client.get_collection_stats(name).get("row_count", 0)) if exists else 0
            stats[name] = {"存在": exists, "数据量": count}
    finally:
        client.close()
    if not any(row["数据量"] > 0 for row in stats.values()):
        raise EvaluationPreflightError("Docker Milvus 目标 collection 均为空")
    return stats
```

- [ ] **Step 4: 实现并发、重试和检查点**

问答阶段使用 `ThreadPoolExecutor(max_workers=concurrency)`，裁判阶段使用独立 executor。每题完成后在锁内追加一行 `dump_chinese(result)` 到 `results.jsonl` 并执行 `flush()` + `os.fsync()`。可重试错误仅包括 `httpx.TimeoutException`、`httpx.TransportError`、HTTP 429/5xx 和临时 Milvus 连接错误；配置与输入错误不重试。

指纹 JSON 固定包含：输入 SHA-256、评分版本、top_n、top_k、问答模型名、裁判模型名、同模型裁判、Milvus URI、collection 名与数据量。使用排序 JSON 的 SHA-256。resume 读取 `run.json` 后先比对指纹，再按 `评测项ID` 跳过已有合法终态；`评测失败` 只重跑裁判，不重复调用问答。

`runner.py` 提供模块入口 `python -m xhbx_rag.evaluation.runner --preflight`：只加载 `RetrievalConfig`、执行上述只读预检、把中文 collection 统计打印到 stdout，成功返回 0，预检失败返回 2；不得启动问答或写入 Milvus。

- [ ] **Step 5: 运行测试并确认通过**

Run: `uv run pytest tests/test_evaluation_runner.py -v`

Expected: 全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add src/xhbx_rag/evaluation/models.py src/xhbx_rag/evaluation/runner.py tests/test_evaluation_runner.py
git commit -m "feat: run resumable docker milvus evaluations"
```

---

### Task 6: 中文报告、CLI 和工作簿安全回填

**Files:**
- Create: `src/xhbx_rag/evaluation/reporting.py`
- Create: `src/xhbx_rag/evaluation/command.py`
- Modify: `scripts/evaluation_workbook.mjs`
- Modify: `src/xhbx_rag/cli.py`
- Modify: `README.md`
- Test: `tests/test_evaluation_reporting.py`
- Test: `tests/test_evaluation_command.py`
- Test: `tests/test_evaluation_workbook_integration.py`

**Interfaces:**
- Consumes: `run.json`、`results.jsonl`、源 XLSX、bundled Node 配置。
- Produces: 中文 Markdown、回填 payload、临时 XLSX、验证 JSON、最终原子替换后的源 XLSX。

- [ ] **Step 1: 编写中文报告、CLI 参数和不覆盖失败的测试**

```python
def test_markdown_report_uses_chinese_headings(tmp_path):
    path = write_markdown_report(tmp_path, make_summary(), make_results())
    text = path.read_text(encoding="utf-8")
    assert "# 问答智能体效果评估报告" in text
    assert "## 总体结论" in text
    assert "同模型裁判：是" in text
    assert "correctness_score" not in text


def test_cli_registers_evaluate_command(monkeypatch):
    monkeypatch.setattr(cli, "run_evaluate_command", lambda args: 0)
    assert cli.main(["evaluate", "--dataset", "input.xlsx", "--output-dir", "out"]) == 0


def test_atomic_backfill_keeps_source_when_verification_fails(tmp_path):
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"original")
    adapter = FailingVerifyAdapter()
    with pytest.raises(ValueError, match="工作簿验证失败"):
        safe_backfill(source, tmp_path / "run", adapter, make_payload())
    assert source.read_bytes() == b"original"
    assert (tmp_path / "run" / "input-backup.xlsx").read_bytes() == b"original"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_evaluation_reporting.py tests/test_evaluation_command.py -v`

Expected: FAIL，错误为缺少 `evaluation.reporting` / `evaluation.command`。

- [ ] **Step 3: 实现中文 Markdown、JSON 和回填 payload**

`reporting.py` 生成：一句话结论、总题数、平均分、保守/有效通过率、优秀率、问答成功率、P50/P95、38/11/1 分层、错误标签频次、代表性低分案例、检索/生成/评测集建议和限制。业务键使用中文；数值保持数值类型。

回填 payload 固定为：

```json
{
  "运行信息": {},
  "汇总指标": {},
  "逐题结果": [
    {
      "Excel行号": 2,
      "智能体回答": "先确认客户预算，再讨论保障缺口。",
      "事实正确性得分": 30,
      "关键点覆盖得分": 18,
      "证据忠实性得分": 17,
      "引用及黄金来源命中得分": 12.5,
      "相关性与表达得分": 9,
      "总分": 86.5,
      "评测等级": "优秀",
      "耗时（秒）": 8.2,
      "主chunk命中": "是",
      "黄金chunk召回率": 0.5,
      "检索chunk_id": "c1；c3",
      "扣分原因": "关键点覆盖基本完整。",
      "错误标签": "关键点缺失",
      "改进建议": "补充下一步行动。",
      "评测状态": "已完成"
    }
  ]
}
```

- [ ] **Step 4: 实现 `backfill` 模式**

`scripts/evaluation_workbook.mjs` 导入源工作簿，保留 A:E 与 `溯源明细`，写入 `绩优案例测试-楚琦!F1:U51`。表头顺序固定：智能体回答、事实正确性得分、关键点覆盖得分、证据忠实性得分、引用及黄金来源命中得分、相关性与表达得分、总分、评测等级、耗时（秒）、主chunk命中、黄金chunk召回率、检索chunk_id、扣分原因、错误标签、改进建议、评测状态。

格式要求：

- 沿用原表表头色系；新增表头白字加粗、自动换行；
- G:L、N、P 为数值，P 使用 `0.0%`，N 使用 `0.00`；
- F、Q:U 左对齐并自动换行；列宽设置上限，避免超宽；
- M 列条件格式：优秀绿色、合格黄色、不合格/问答失败/评测失败红色；
- `评测总览` 使用公式引用 `'绩优案例测试-楚琦'!$L$2:$L$51` 和 `$M$2:$M$51` 计算总题数、平均分、合格/优秀率；
- `低分与错误案例` 写入等级非优秀/合格或状态非已完成的题；
- `运行元数据` 不写 API key、token 或绝对用户目录；
- 新增工作表先 `deleteAllDrawings()` 并清理 used range，再幂等重建；不创建重复同名表。

- [ ] **Step 5: 实现 `verify` 模式与安全替换**

`verify` 重新导入临时 XLSX，检查：5 个工作表存在；主表 51 行；F:U 中文表头完全一致；50 条结果均有评测状态；A:E 与抽取快照逐单元格相等；`溯源明细` 与快照逐单元格相等；公式错误扫描为 0；渲染 5 个工作表到运行目录 `预览/`。输出中文 `工作簿验证.json`。

`safe_backfill()` 顺序固定：`shutil.copy2(source, backup)` → 生成 `待验证.xlsx` → verify → `os.replace(待验证.xlsx, source)`。任何异常都不得先修改 source。

- [ ] **Step 6: 注册 CLI**

在 `_build_parser()` 增加 `evaluate`：`--dataset Path required`、`--output-dir Path default outputs/evaluations`、`--concurrency int default 2`、`--judge-concurrency int default 2`、`--top-n int default 20`、`--top-k int default 5`、`--limit int`、`--item-id action=append`、`--resume`、`--no-xlsx`。在 `main()` 中调用 `run_evaluate_command(args)`。

`command.py` 返回：完成为 0；输入/配置/Milvus/工作簿预检失败为 2；运行级落盘失败为 3。不合格题不会改变进程退出码。

- [ ] **Step 7: 运行 Python 测试并确认通过**

Run: `uv run pytest tests/test_evaluation_reporting.py tests/test_evaluation_command.py -v`

Expected: 全部 PASS。

- [ ] **Step 8: 在临时副本上运行真实工作簿集成测试**

```bash
export EVALUATION_NODE_BIN=/Users/milan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
export EVALUATION_ARTIFACT_NODE_MODULES=/Users/milan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules
uv run pytest tests/test_evaluation_workbook_integration.py -v
```

Expected: PASS；只修改 `tmp_path` 副本；A:E 与 `溯源明细` 一致；F:U 与三个新增工作表均为中文；5 张预览图生成；公式错误为 0。

- [ ] **Step 9: 更新 README 并提交**

README 写明：只启动 `docker compose up -d standalone`；设置 bundled Node 环境；`evaluate` 示例；50 条全部计分；同模型裁判标记；回填前备份；不会自动改写 Milvus。

```bash
git add src/xhbx_rag/evaluation/reporting.py src/xhbx_rag/evaluation/command.py scripts/evaluation_workbook.mjs src/xhbx_rag/cli.py README.md tests/test_evaluation_reporting.py tests/test_evaluation_command.py tests/test_evaluation_workbook_integration.py
git commit -m "feat: report and backfill qa evaluations"
```

---

### Task 7: Docker 冒烟、50 条全量评测、独立复核与最终验收

**Files:**
- Modify: `docs/新华保险AI教练问答一批绩优案例测试集-含完整溯源.xlsx`
- Create: `outputs/evaluations/<run_id>/run.json`
- Create: `outputs/evaluations/<run_id>/results.jsonl`
- Create: `outputs/evaluations/<run_id>/report.md`
- Create: `outputs/evaluations/<run_id>/input-backup.xlsx`
- Create: `outputs/evaluations/<run_id>/工作簿验证.json`
- Create: `outputs/evaluations/<run_id>/预览/*.png`

**Interfaces:**
- Consumes: Tasks 1–6 完成的 CLI、Docker Compose、真实 `.env` 和 50 条评测集。
- Produces: 已回填工作簿、中文效果报告、机器结果、独立质量复核结论。

- [ ] **Step 1: 运行评测模块测试和差异检查**

```bash
uv run pytest tests/test_evaluation_models.py tests/test_evaluation_dataset.py tests/test_evaluation_metrics.py tests/test_evaluation_judge.py tests/test_evaluation_runner.py tests/test_evaluation_reporting.py tests/test_evaluation_command.py -q
git diff --check
```

Expected: 评测测试全部 PASS；`git diff --check` 无输出。

- [ ] **Step 2: 启动 Docker Milvus 并等待健康**

```bash
docker compose up -d standalone
docker compose ps
```

Expected: `etcd`、`minio`、`standalone` 均为 `healthy`；不启动 `api` 和 `web`。

- [ ] **Step 3: 执行预检并记录 collection 数据量**

Run: `uv run python -m xhbx_rag.evaluation.runner --preflight`

Expected: 退出码 0；`MILVUS_MODE=docker`；至少一个 collection 数据量大于 0；不执行写入。

- [ ] **Step 4: 运行 2 条真实冒烟且不回填工作簿**

```bash
export EVALUATION_NODE_BIN=/Users/milan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
export EVALUATION_ARTIFACT_NODE_MODULES=/Users/milan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules
uv run xhbx-rag evaluate --dataset "docs/新华保险AI教练问答一批绩优案例测试集-含完整溯源.xlsx" --output-dir outputs/evaluations --concurrency 2 --judge-concurrency 2 --top-n 20 --top-k 5 --limit 2 --no-xlsx
```

Expected: 退出码 0；2 条均有智能体回答、检索证据、规则分和中文裁判结果；JSONL 无英文业务字段。

- [ ] **Step 5: 运行全部 50 条并安全回填目标工作簿**

```bash
uv run xhbx-rag evaluate --dataset "docs/新华保险AI教练问答一批绩优案例测试集-含完整溯源.xlsx" --output-dir outputs/evaluations --concurrency 2 --judge-concurrency 2 --top-n 20 --top-k 5
```

Expected: 退出码 0；50 条全部有终态；源文件在验证通过后原子替换；运行目录包含备份、中文 JSON/JSONL/Markdown、验证 JSON 和 5 张预览图。

- [ ] **Step 6: 执行独立数据一致性验证**

检查：结果条数 50；Excel 行号 2–51 各一次；保守通过率分母 50；五维分数范围合法；总分等于五维之和；等级阈值正确；38/11/1 分层数量不变；问答失败为 0 分；裁判失败无伪造分数；备份 SHA-256 等于回填前源文件；A:E 和 `溯源明细` 与备份逐单元格一致。

- [ ] **Step 7: 执行独立子代理双阶段复核**

规格复核代理检查 Tasks 1–6 是否逐项满足设计；质量复核代理抽查至少 10 条（优秀、合格、不合格、完整支持、部分支持、未定位均覆盖），核对裁判理由与真实回答/证据一致，检查中文字段和管理层结论未夸大。发现问题时回到对应任务修复并重新运行相关测试与报告构建。

- [ ] **Step 8: 完整回归和工作簿视觉验收**

```bash
uv run pytest -q
cd web && npm test -- --run && npm run build
git diff --check
git status --short
```

Expected: Python 全套、前端测试和构建通过；公式错误为 0；5 个工作表预览无截断、重叠、不可读表头或错误颜色；除用户工作簿、评测输出和有意代码改动外无意外文件。

- [ ] **Step 9: 提交代码修正并记录交付信息**

如果 Tasks 7–8 产生代码修正，按模块提交；`outputs/evaluations/`、备份、预览和用户工作簿不因代码提交而被删除。最终记录代码 commit、评测 run_id、工作簿 SHA-256、50 条状态分布和主要效果指标。

---

## 最终自检清单

- [ ] 设计中的 CLI、本地问答、Docker Milvus、独立裁判、50 条全计分、中文输出、断点续跑、备份、原子替换和五工作表要求都有对应任务。
- [ ] 计划中没有占位实现或未定义接口。
- [ ] `EvaluationItem`、`JudgeResult`、`DeterministicScores`、`EvaluationResult` 在相邻任务中的命名一致。
- [ ] 确定性 15 分与裁判 85 分的字段和阈值在测试、代码、Excel、Markdown 中一致。
- [ ] 未定位题只使用确定性的检索证据有效性替代规则，不把裁判结果混进规则分。
- [ ] JSON/JSONL 用中文字段别名；Excel/Markdown 用中文展示值；技术标识不被错误翻译。
- [ ] XLSX 全流程只使用 bundled `@oai/artifact-tool`，并在临时目录链接 loader 提供的 node_modules。
- [ ] 回填失败时原文件保持不变；成功时备份可恢复且 A:E、`溯源明细` 不变。
