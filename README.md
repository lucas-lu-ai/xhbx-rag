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

## 一键入库（generate-insights → parse → index）

如果想把一个案例素材目录一条命令直通向量库，可以用 `ingest`：

```bash
uv run xhbx-rag ingest \
  --case-dir "data/绩优案例/案例A" \
  --generated-out generated \
  --parsed-out parsed \
  --index-mode incremental \
  --stream --reuse-section-evidence --no-thinking --trace
```

`ingest` 支持 `generate-insights` 的全部参数（`--stream`、`--no-thinking`、`--reuse-section-evidence`、`--case-call-mode` 等）。执行语义：

- generate 阶段 `failed` 时立即退出（parse/index 不执行）；`partial` 时继续入库已产出的知识类型
- parse 失败会写 `parse_report.json` 并退出，不触碰索引
- 结束时输出三阶段汇总 JSON（generate 状态与 `case_part_errors`、parse 计数、index 写入条数）
- 重新生成同一案例后建议 `--index-mode rebuild`，避免 chunk_id 漂移导致新旧并存

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
- 对接口/网络错误走模型客户端重试（流式响应中途断连也在重试范围内）；对结构化内容错误，会把校验错误和上次输出加入上下文后继续请求模型修复
- 对章节失败做隔离并写入 `*.sales_evidence.failed.json`，只要还有成功章节，就继续汇总输出案例级 JSON 和 MD
- 给模型的素材会带 `L001` 形式的行号；模型可以写归纳后的 `quote`，但必须在 `locator.line_start/line_end` 标明支撑它的原文行
- 使用结构化 tool-call 生成节级/案例级 JSON，默认开启 Qwen 思考模式；如需快速调试或网络不稳可加 `--no-thinking`

案例级汇总默认按知识类型拆成 4 次小调用（`--case-call-mode split`）：

- 先把全部章节证据本地编成带短 ID（`E001`）的证据目录并做精确去重，只把知识主文本喂给模型；模型产出只引用 `evidence_ids`，完整的 `evidence_refs`（含 locator）由本地按 ID 回挂，不经模型抄写
- 4 次调用依次产出 `case_summary+customer_journey`、`strategies`、`scripts`、`objection_handling`；单个类型失败不影响其余类型，结果状态为 `partial` 并在 `case_part_errors` 中列出失败原因，仍会写出可用的 insights 与 playbook
- 每个类型成功后在 `generated/<case>/case.insights_parts/` 写入 checkpoint（带输入指纹）；重跑时输入未变的类型直接复用，不再请求模型
- `--case-call-mode single` 可回退到旧的单次大调用
- `--reuse-section-evidence` 可复用输出目录中已有的合法章节 `sales_evidence.json`，跳过章节级模型抽取（配合 checkpoint 实现断点续跑）

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

## MCP 检索服务

如果希望在 Claude Code、Claude Desktop 等 MCP 客户端里直接检索知识库，可以启动内置的 MCP 服务：

```bash
uv run xhbx-rag-mcp                              # stdio（默认，供本机客户端）
uv run xhbx-rag-mcp --transport streamable-http  # HTTP（供远程客户端，默认 127.0.0.1:8000/mcp）
```

服务对外提供两个工具：

- `search_knowledge(query, top_n=20, top_k=5)`：完整复用 `search` 的检索链（query understanding → 向量 + 关键词混合召回 → RRF 融合 → rerank），返回证据 chunk（含知识类型、原文引用与定位）。`top_n` 范围 1–100，`top_k` 范围 1–20 且不能大于 `top_n`。
- `retrieval_status()`：返回 Milvus 模式与目标、collection 名称和必要配置是否齐全，不包含任何密钥内容。

项目根目录的 `.mcp.json` 已注册该服务，在本仓库中打开 Claude Code 即可直接使用。服务从项目目录的 `.env` 读取配置，需要与 `search` 命令相同的模型、embedding、rerank 与 Milvus 配置；对外错误消息经过白名单过滤，不泄漏内部路径与堆栈。lite 模式下索引是本地单进程文件，MCP 服务与 Web 服务不能同时打开同一个索引文件。

## Web 问答界面

如果希望在浏览器里直接问答并查看溯源，可以启动本机 Web 界面。第一版只读取已有索引，不在 Web 内执行 `generate-insights -> parse -> index`。

后端：

```bash
uv run uvicorn xhbx_rag.web.app:app --reload --host 127.0.0.1 --port 8000
```

前端：

```bash
cd web
npm install
npm run dev
```

打开 `http://localhost:5173`。

原始数据文件应放在项目 `data/` 目录下。点击引用时，页面会展示 `source_path`、来源类型、locator、原文摘录和定位置信心。右侧“检索证据”会展示本次进入回答模型的 evidence chunks，方便对照回答是否被完整证据支撑。点击“在 Finder 中显示文件”只会打开 `data/` 目录内的本地文件；第一版不会尝试把 Word/PPT/PDF 精确跳转到段落或页内锚点。

### 批量执行会话

批量执行在服务端后台运行，输入、逐行状态、回答结果与行级 bad case 反馈全部持久化到 SQLite（`.local/web_batch/batch_runs.sqlite3`）。侧栏“批量执行”按钮进入创建视图，上传 txt/csv/xlsx 或粘贴内容、解析后点“开始批量运行”即创建一个批量会话；之后可以随时新建聊天会话继续问答，稍后切回批量会话查看进度与结果，刷新页面也不会丢失。

- 批量会话与聊天会话统一显示在左侧侧栏，批量条目带“批量”徽标、状态与进度。
- 失败行可单独重试；导出“回填文件”和“bad case JSONL”从服务端数据构建。
- 后端重启（包括 `--reload` 触发的重启）会把运行中的批次标记为“已中断”，不会自动续跑（避免无人确认地消耗模型 API 费用），在批量会话顶部点“继续执行”即可完成剩余行。
- 批量执行依赖进程内后台线程，Web 服务只支持单个 uvicorn worker 进程（默认启动方式即是）。
- lite 模式下批量行与单问共用一把进程内锁串行执行，批量运行期间单问会排队等待。

Web 问答默认会把处理过程通过 SSE 推到前端。如果还要在 AgentScope Studio 里观测同一轮消息，先启动 Studio，然后在 `.env` 中打开 Web trace：

```env
WEB_STUDIO_TRACE=true
WEB_STUDIO_ENDPOINT=localhost:4317
```

`WEB_STUDIO_TRACE` 默认为关闭；打开后，每次 Web 发送消息都会创建一条 `xhbx-rag.web.answer` root trace，同时保留前端页面里的实时处理过程。

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

Milvus：

```env
MILVUS_MODE=lite
MILVUS_LITE_PATH=.local/milvus/xhbx_rag.db
MILVUS_URI=http://localhost:19530
MILVUS_TOKEN=
MILVUS_COLLECTION=xhbx_sales_chunks
MILVUS_VECTOR_DIM=
```

Web AgentScope Studio trace：

```env
WEB_STUDIO_TRACE=false
WEB_STUDIO_ENDPOINT=localhost:4317
```

Web 批量执行并发数：

```env
WEB_BATCH_CONCURRENCY=3
```

`WEB_BATCH_CONCURRENCY` 控制 Web 批量问答同时执行的问题数（1-10，默认 3），仅在 `MILVUS_MODE=docker` 时生效；Milvus Lite 是单进程本地文件，lite 模式下批量执行强制串行。

`MILVUS_MODE` 默认为 `lite`，继续使用 `MILVUS_LITE_PATH` 指向的 Milvus Lite 本地文件。

如需连接本机 Docker Milvus：

```env
MILVUS_MODE=docker
MILVUS_URI=http://localhost:19530
MILVUS_TOKEN=
```

如果 Docker Milvus 启用了鉴权，把 `MILVUS_TOKEN` 设置为对应 token；未启用鉴权时留空。本项目只根据配置选择连接方式，不负责启动 Docker 服务。

`MILVUS_VECTOR_DIM` 可以留空。首次入库时会根据 embedding 返回向量长度自动创建 collection。
