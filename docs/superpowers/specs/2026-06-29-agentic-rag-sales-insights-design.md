# xhbx-rag 销售洞察 Agentic RAG 设计

日期：2026-06-29

## 1. 背景

`xhbx-rag` 是 `xhbx` 的下游项目。上游 `xhbx` 会产出销售洞察文件：

- `case.sales_insights.json`
- `case.sales_playbook.md`

本项目不解析上游原始素材，不调用上游智能体，也不关心上游流水线如何生成这些文件。用户会直接上传上述文件，本项目负责把它们解析为后续 RAG 可消费的标准化数据。

AgentScope 使用当前最新的 `2.0.3` 版本。AgentScope 2.x 中 ReAct 由统一 `Agent` 配合 `ReActConfig`、`Toolkit` 或 `RAGMiddleware` 实现，不依赖旧式单独 `ReActAgent` 类。

## 2. 第一版目标

第一版只实现“销售洞察文件解析与 RAG 入库前产物生成”。

输入：

- 必需：`case.sales_insights.json`
- 可选：`case.sales_playbook.md`

输出：

- `parsed/<case_id>/case.structured.json`
- `parsed/<case_id>/chunks.jsonl`
- `parsed/<case_id>/parse_report.json`

第一版不实现：

- 原始 `docx` / `pptx` / `pdf` / `txt` 解析
- 调用 `/Users/milan/xhbx` 生成销售洞察
- embedding
- 向量库写入
- 问答 API
- 权限控制
- ReAct 查询执行

## 3. 项目结构

使用 `uv` 管理 Python 环境。

```text
xhbx-rag/
├── pyproject.toml
├── README.md
├── src/xhbx_rag/
│   ├── __init__.py
│   ├── cli.py
│   ├── models.py
│   ├── parser.py
│   ├── normalizer.py
│   ├── chunk_builder.py
│   ├── writer.py
│   └── report.py
└── tests/
```

CLI 第一版只提供解析命令：

```bash
uv run xhbx-rag parse \
  --insights uploads/case.sales_insights.json \
  --playbook uploads/case.sales_playbook.md \
  --out parsed
```

`--playbook` 可省略。缺少 `case.sales_playbook.md` 不应导致解析失败。

## 4. 数据流

```text
uploads/
  case.sales_insights.json
  case.sales_playbook.md
      ↓
Parser
  校验 JSON 基本结构
  读取 playbook 作为辅助校验来源
  统一 source_file / evidence_refs
      ↓
Normalizer
  生成 StructuredCaseKnowledge
  保留客户旅程、销售策略、场景话术、异议处理四类知识
      ↓
Chunk Builder
  每个旅程步骤、策略、话术、异议处理生成独立 RagChunk
      ↓
Writer
  写出 case.structured.json
  写出 chunks.jsonl
  写出 parse_report.json
```

## 5. 核心数据模型

### 5.1 StructuredCaseKnowledge

规范化后的案例知识对象，保留四类结构化知识：

- `customer_journey`
- `strategies`
- `scripts`
- `objection_handling`

顶层字段：

- `case_id`：由 `case_name` 生成的稳定文件安全标识
- `case_name`
- `case_summary`
- `source_files`
- `customer_journey`
- `strategies`
- `scripts`
- `objection_handling`

### 5.2 RagChunk

面向后续向量入库的最小 chunk。

字段：

- `chunk_id`
- `chunk_type`：`customer_journey`、`strategy`、`script`、`objection_handling`
- `text`：可直接送 embedding 的干净文本
- `metadata`
- `citations`
- `source_file`

示例：

```json
{
  "chunk_id": "mi_li_xia_bai_wan_biao_bao__script__script_001",
  "chunk_type": "script",
  "text": "阶段：售前\n场景：客户不想聊保险\n客户触发点：客户抗拒谈保险\n目标：打开话题\n原始话术：保险离你想守护的家庭责任并不远。\n教练推荐话术：先确认客户当前最担心的家庭责任，再用风险缺口引导客户表达真实顾虑。",
  "metadata": {
    "case_name": "米丽霞的百万标保销售系统",
    "stage": "售前",
    "scenario": "客户不想聊保险",
    "customer_trigger": "客户抗拒谈保险",
    "strategy_names": ["风险唤醒"]
  },
  "citations": [
    {
      "section_name": "第1节",
      "filename": "讲义.txt",
      "quote": "客户不想聊保险时，先从家庭责任和风险缺口切入。"
    }
  ],
  "source_file": "case.sales_insights.json"
}
```

### 5.3 ParseReport

解析报告用于审计和调试。

字段：

- `input_files`
- `output_files`
- `case_name`
- `counts`
- `warnings`
- `errors`

字段缺失但可安全默认时写入 `warnings`。关键字段缺失时解析失败并写入 `errors`。

## 6. 解析规则

`case.sales_insights.json` 是主事实来源。

`case.sales_playbook.md` 是可选辅助来源：

- 用于记录输入文件存在性
- 可用于校验标题中的案例名是否与 JSON 一致
- 可用于后续人工检查
- 不覆盖 JSON 字段
- 不反向推断 JSON 中不存在的事实

字段处理：

- `case_name` 缺失：失败
- 顶层 JSON 非对象：失败
- `customer_journey` / `strategies` / `scripts` / `objection_handling` 缺失：默认空列表并写 warning
- 条目内非关键字段缺失：默认空字符串或空列表并写 warning
- `evidence_refs` 缺失：默认空列表并写 warning

## 7. Chunk 构造规则

每个结构化对象生成一个独立 chunk。

客户旅程 chunk 包含：

- 阶段
- 客户状态
- 销售目标
- 关键动作
- 来源依据

销售策略 chunk 包含：

- 策略名称
- 别名
- 定义
- 适用阶段
- 步骤
- 建议做法
- 避免做法
- 置信度
- 是否模型归纳
- 来源依据

场景话术 chunk 包含：

- 话术 ID
- 阶段
- 场景
- 客户触发点
- 目标
- 原始话术
- 教练推荐话术
- 关联策略
- 追问建议
- 合规提醒
- 来源依据

异议处理 chunk 包含：

- 客户异议
- 异议诊断
- 推荐回应
- 关联策略
- 关联话术
- 来源依据

`text` 字段应面向语义检索，避免 JSON 噪声；`metadata` 字段应面向过滤和精确检索。

## 8. 查询层预留设计

查询层不在第一版实现，但第一版输出必须为后续查询层预留字段。

后续查询链路：

```text
用户问题
  ↓
QueryRewriteAgent
  输出独立、明确、适合检索的 query
  抽取 stage / scenario / objection / strategy 等过滤线索
  ↓
QueryRouter
  判断走结构化检索、向量检索，还是混合检索
  ↓
受控 ReAct RAG Agent
  使用 AgentScope Agent + ReActConfig
  可调用 search_knowledge / search_structured_sales_insights
  ↓
Evidence Grader
  判断证据是否足够
  ↓
Answer Generator
  基于证据回答并返回引用
```

Query rewrite 的职责：

- 把多轮、省略、口语化问题改写成独立检索 query
- 保留用户原意，不增加未出现的事实条件
- 提取可用于过滤的结构化线索
- 同时保留 `original_query` 和 `rewritten_query`

ReAct 使用边界：

- 只用于检索和证据补充
- 工具只读
- `ReActConfig.max_iters` 建议为 `3` 或 `5`
- 不允许无限循环
- 不做写操作、外部系统调用或业务动作
- 证据不足时返回无法确认，而不是编造

后续可提供的工具：

- `search_structured_sales_insights`：基于结构化 JSON 做阶段、场景、策略、异议过滤
- `search_knowledge`：基于向量库或 AgentScope RAGMiddleware 做语义检索

## 9. 错误处理

解析错误分为三类：

- `fatal`：无法继续，例如 JSON 非法、`case_name` 缺失
- `warning`：可默认处理，例如某类列表缺失
- `notice`：非阻断信息，例如 playbook 未提供

CLI 行为：

- fatal：退出码非 0
- warning：退出码 0，但写入 `parse_report.json`
- notice：退出码 0，写入 `parse_report.json`

## 10. 测试策略

单元测试覆盖：

- 合法 `case.sales_insights.json` 解析
- 缺少 playbook 仍成功
- 缺少四类列表时默认空列表并 warning
- 缺少 `case_name` 时失败
- 每类对象生成正确 chunk
- `chunks.jsonl` 每行都是合法 JSON
- `case_id` 和 `chunk_id` 稳定
- `citations` 保留 `evidence_refs`

不在第一版测试中覆盖：

- 真实 LLM 调用
- embedding
- 向量库
- AgentScope ReAct 查询循环

## 11. 验收标准

给定一组 `case.sales_insights.json` 和可选 `case.sales_playbook.md`：

1. `uv sync` 成功安装环境。
2. `uv run xhbx-rag parse --insights <json> --playbook <md> --out parsed` 成功运行。
3. 输出 `case.structured.json`、`chunks.jsonl`、`parse_report.json`。
4. `chunks.jsonl` 中每个 chunk 有稳定 `chunk_id`、可检索 `text`、过滤用 `metadata` 和来源 `citations`。
5. 测试不联网、不调用真实模型。
