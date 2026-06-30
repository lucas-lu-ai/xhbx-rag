# xhbx-rag

`xhbx-rag` 用于从保险绩优案例素材生成销售洞察产物，并生成后续 RAG 入库前可直接消费的文件。

当前支持两类输入：

- 原始案例素材目录：按案例/节目录组织的 `docx / pptx / pdf / txt`
- `case.sales_insights.json`
- `case.sales_playbook.md`（可选）

当前项目不调用上游 `xhbx` 项目。它内置了销售洞察生成流程、RAG chunk 生成、Milvus Lite 本地入库、query 改写、向量召回、BM25 关键词召回、RRF 融合、rerank 和基于证据的回答。

## 环境准备

```bash
uv sync
```

## 生成销售洞察

如果输入是原始案例素材目录，可以先生成 `case.sales_insights.json` 与 `case.sales_playbook.md`：

```bash
uv run xhbx-rag generate-insights \
  --case-dir "uploads/【郭春渝】专注保单整理，达成百万业绩" \
  --out generated \
  --trace
```

案例目录会按“一个含素材文件的目录 = 一个章节”加载。支持：

- `txt`：保留文本行号，引用可定位到 `Lx-Ly`
- `docx`：提取段落、标题和表格，引用可定位到解析文本行号和标题路径
- `pptx`：按幻灯片提取文本，引用可定位到 `slideN` 和行号
- `pdf`：按页提取文本，引用可定位到 `pN` 和行号

生成完成后会在 `generated/<safe_case_name>/` 下写入：

- `*.sales_evidence.json`：节级销售证据 sidecar
- `*.sales_evidence.failed.json`：节级生成失败记录，不参与后续 parse/index
- `case.sales_insights.json`：案例级客户旅程、销售策略、话术和异议处理
- `case.sales_playbook.md`：面向人工审阅的销售洞察手册

生成阶段默认会：

- 去掉同一章节目录里的完全重复素材副本，例如 `xxx.txt` 和 `xxx(1).txt`
- 按章节并发生成节级证据，并在每个章节内按单个来源文件逐次请求模型，再合并为一个节级证据
- 对单个来源文件失败做隔离，其他来源仍会继续处理
- 对接口/网络错误走模型客户端重试；对结构化内容错误，会把校验错误和上次输出加入上下文后继续请求模型修复
- 对章节失败做隔离并写入 `*.sales_evidence.failed.json`，只要还有成功章节，就继续汇总输出案例级 JSON 和 MD
- 给模型的素材会带 `L001` 形式的行号；模型可以写归纳后的 `quote`，但必须在 `locator.line_start/line_end` 标明支撑它的原文行
- 使用结构化 tool-call 生成节级/案例级 JSON，默认开启 Qwen 思考模式；如需快速调试或网络不稳可加 `--no-thinking`

网络不稳或模型响应慢时，可以调这些参数：

```bash
uv run xhbx-rag generate-insights \
  --case-dir "uploads/案例A" \
  --out generated \
  --timeout 600 \
  --retry-attempts 3 \
  --section-concurrency 3 \
  --max-section-chars 9000 \
  --trace
```

`evidence_refs` 会尽量带上原文位置字段，例如：

```json
{
  "filename": "第1节.track-0.txt",
  "quote": "客户每年保费预算不能超过80万",
  "context": "老师开场\n客户说每年不能超过80万\n销售回应可以看缴费期满的保单",
  "source_excerpt": "客户说每年不能超过80万",
  "source_type": "txt",
  "source_path": "案例A/第1节/第1节.track-0.txt",
  "locator": {
    "line_start": 2,
    "line_end": 2,
    "char_start": 4,
    "char_end": 17
  },
  "locator_confidence": "validated_span",
  "locator_error": "",
  "anchor_id": "txt:第1节.track-0.txt#line-2"
}
```

如果模型给出的行号不合法，且 `quote` 也无法回查到原文，系统会尽量补上
`source_path/source_type`，并将 `locator_confidence` 标记为 `unmatched`、
`locator_error` 标明失败原因。已有旧 JSON 不会自动补全这些字段，需要重新执行
`generate-insights -> parse -> index`。

## 解析销售洞察

```bash
uv run xhbx-rag parse \
  --insights generated/郭春渝_专注保单整理_达成百万业绩/case.sales_insights.json \
  --playbook generated/郭春渝_专注保单整理_达成百万业绩/case.sales_playbook.md \
  --out parsed
```

`--playbook` 可以省略。解析完成后会在 `parsed/<case_id>/` 下生成：

- `case.structured.json`：规范化后的结构化销售知识
- `chunks.jsonl`：面向后续向量检索的 RAG chunk
- `parse_report.json`：解析报告、统计信息和 warning/error

推荐端到端流程：

```bash
uv run xhbx-rag generate-insights --case-dir uploads/案例A --out generated
uv run xhbx-rag parse --insights generated/案例A/case.sales_insights.json --playbook generated/案例A/case.sales_playbook.md --out parsed
uv run xhbx-rag index --chunks parsed/<case_id>/chunks.jsonl
uv run xhbx-rag answer --query "客户说每年不能超过80万怎么办？" --top-n 20 --top-k 5
```

## Milvus Lite 本地入库

解析得到 `chunks.jsonl` 后，可以先写入本地 Milvus Lite 做检索验证：

```bash
uv run xhbx-rag index \
  --chunks parsed/案例a_b2bb7fa579/chunks.jsonl
```

默认入库模式是增量更新：

```bash
uv run xhbx-rag index \
  --chunks parsed/案例a_b2bb7fa579/chunks.jsonl \
  --mode incremental
```

`incremental` 会使用 `chunk_id` 做 upsert：同一个 `chunk_id` 会被覆盖，不同 `chunk_id`
会继续保留。如果希望清空当前 Milvus collection 的所有内容后再重新入库，使用：

```bash
uv run xhbx-rag index \
  --chunks parsed/案例a_b2bb7fa579/chunks.jsonl \
  --mode rebuild
```

本地检索命令：

```bash
uv run xhbx-rag search \
  --query "客户不想聊保险怎么开场？" \
  --top-n 20 \
  --top-k 5
```

调试每一步运行结果时可以打开 trace：

```bash
uv run xhbx-rag search \
  --query "客户不想聊保险怎么开场？" \
  --top-n 20 \
  --top-k 5 \
  --trace
```

`--trace` 会把步骤事件按 JSONL 写到 `stderr`，最终检索结果仍写到 `stdout`，便于脚本继续解析最终 JSON。当前会输出这些关键步骤：

- `search.query_received`：原始问题和 topN/topK 参数
- `search.query_understood`：意图识别、query 改写、过滤条件
- `search.query_embedded`：被向量化的改写 query、向量维度和向量前几个数值
- `search.vector_searched`：Milvus 过滤条件、候选数量和候选 chunk 预览
- `search.reranked`：rerank 后的 chunk 顺序和分数
- `search.completed`：最终结果数量

索引命令也支持 `--trace`：

```bash
uv run xhbx-rag index \
  --chunks parsed/案例a_b2bb7fa579/chunks.jsonl \
  --trace
```

## AgentScope Studio 可视化

如果希望在可视化界面里查看每一步执行结果，可以先启动 AgentScope Studio：

```bash
npm install -g @agentscope/studio
as_studio
```

默认情况下，Studio Web UI 在 `http://localhost:3000`，OTLP gRPC trace endpoint 在 `localhost:4317`。

检索时打开 Studio trace：

```bash
uv run xhbx-rag search \
  --query "客户不想聊保险怎么开场？" \
  --top-n 20 \
  --top-k 5 \
  --studio
```

索引时也可以打开 Studio trace：

```bash
uv run xhbx-rag index \
  --chunks parsed/案例a_b2bb7fa579/chunks.jsonl \
  --studio
```

如果 Studio 使用了不同的 OTLP gRPC 地址，可以显式指定：

```bash
uv run xhbx-rag search \
  --query "客户不想聊保险怎么开场？" \
  --studio \
  --studio-endpoint localhost:4317
```

`--studio` 和 `--trace` 可以同时使用：前者把 span 发到 AgentScope Studio，后者把 JSONL 写到 `stderr`。

`search` 不会直接向量化原始 query。流程是：

1. 调用 query understanding，把原始问题改写成 `rewritten_query` 并抽取过滤条件。
2. 只向量化 `rewritten_query`。
3. 使用 Milvus Lite 向量召回 topN。
4. 使用本地 BM25 关键词召回 topN。
5. 使用 RRF 合并去重向量候选和关键词候选。
6. 使用 rerank API 重排。
7. 输出 topK evidence chunks。

打开 `--trace` 时，混合检索会额外输出：

- `search.keyword_searched`：BM25 关键词召回候选
- `search.hybrid_fused`：RRF 融合后的候选

## 基于检索结果生成回答

如果需要一条命令完成检索和回答，可以使用 `answer`：

```bash
uv run xhbx-rag answer \
  --query "保单整理对客户有什么作用？" \
  --top-n 20 \
  --top-k 5
```

`answer` 会先复用 `search` 的 query understanding、向量召回和 rerank 流程，再把 topK evidence chunks 交给回答整合节点。最终输出 JSON：

- `answer`：只基于检索证据生成的中文回答
- `citations`：支撑回答的来源引用；如果生成阶段成功定位，会包含 `source_type/source_path/locator/locator_confidence/anchor_id`
- `evidence_count`：本次用于回答的 evidence chunk 数量

证据不足或问题不属于检索范围时，`answer` 会返回“当前检索结果不足以确认。”

## 环境变量

对话模型：

```env
API_KEY=
BASE_URL=
MODEL_NAME=
```

SiliconFlow embedding：

```env
EMBEDDING_BASE_URL=https://api.siliconflow.com/v1
EMBEDDING_MODEL_NAME=Qwen/Qwen3-Embedding-8B
EMBEDDING_API_KEY=
```

SiliconFlow rerank：

```env
RERANK_BASE_URL=https://api.siliconflow.com/v1
RERANK_MODEL_NAME=Qwen/Qwen3-Reranker-8B
RERANK_API_KEY=
```

外部 HTTP 调用默认会对临时网络错误重试 3 次，覆盖模型、embedding 和 rerank 请求。当前会重试连接断开、TLS/连接错误、读取超时，以及 429/5xx 响应。

Milvus Lite：

```env
MILVUS_LITE_PATH=.local/milvus/xhbx_rag.db
MILVUS_COLLECTION=xhbx_sales_chunks
MILVUS_VECTOR_DIM=
```

`MILVUS_VECTOR_DIM` 可以留空。首次入库时会根据 embedding 返回向量长度自动创建 collection。
