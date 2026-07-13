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
  --case-dir "uploads/xxx案例" \
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
  --insights generated/xxxx/case.sales_insights.json \
  --playbook generated/xxxx/case.sales_playbook.md \
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

## 培训课程知识入库（parse-course / ingest-course）

除绩优案例外，还支持把培训课程资产（制式课件、教材、书稿）入库为独立的课程知识库。课程管线不走案例级 LLM 抽取，而是**规则切块**：pptx 按页切（正文 + 讲师备注合并，备注中"教学时间/教学方式"剔除、"教学目标"进 metadata），docx 按标题层级切，pdf 按页切；每门课额外产出一个课程概览 chunk。

```bash
# 一键：解析切块 → 写入课程库（MILVUS_COURSE_COLLECTION）
uv run xhbx-rag ingest-course --course-dir "data/培训数据" --trace

# 分步：先看切块产物，再入库
uv run xhbx-rag parse-course --course-dir "data/培训数据" --out parsed_courses --no-enrich
uv run xhbx-rag index --chunks parsed_courses/chunks.jsonl --collection course
```

- 默认开启课程级 LLM 增值（每门课一次小调用生成摘要、受众与销售环节标签），`--no-enrich` 可关闭；增值失败自动降级为纯规则产物并计入报告，不阻塞入库。
- 扫描时自动跳过 `~$` 临时文件、隐藏文件与不支持的扩展名；单个文件解析失败不拖垮整批（记入 `parse_report.json` 的 `failed_files`）。
- `parse_report.json` 会统计内容完全相同的重复文件（`duplicate_text_hashes`），供人工清理。
- doc/ppt/wps 老格式需先转换（依赖本机 LibreOffice）：

```bash
uv run python scripts/convert_legacy_formats.py --dir "data/培训数据" --dry-run   # 先看计划
uv run python scripts/convert_legacy_formats.py --dir "data/培训数据"             # 执行转换
```

检索时案例库与课程库**聚合召回**：向量召回按分数跨库合并，BM25 关键词召回把两库候选合池后统一打分，聚合后走同一条 RRF 融合 → 标签软加权 → rerank 链路。`search / answer` / Web / MCP 无需额外参数。

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
uv run xhbx-rag-mcp --transport streamable-http  # HTTP（供远程客户端，默认 http://127.0.0.1:8000/mcp）
uv run xhbx-rag-mcp --transport streamable-http --host 0.0.0.0 --port 9331   # 自定义监听地址与端口
uv run xhbx-rag-mcp --transport sse --path /mcp/sse --port 9331              # 兼容旧版 HTTP+SSE 协议客户端
```

`--host/--port/--path` 仅对 HTTP 类传输（`streamable-http` / `sse`）生效。`--path` 用于适配客户端固定拼接的端点路径：`streamable-http` 默认 `/mcp`，`sse` 默认 `/sse`；如果调用方框架自动在 `ip:端口` 后拼 `/mcp/sse`，用上面的 sse 示例即可。服务本身无鉴权，把 `--host` 绑定到非回环地址（如 `0.0.0.0`）前请确认处于可信内网，不要暴露到公网。

服务对齐《知识库 MCP Tool 文档》平台契约（见 `docs/知识库 MCP Tool 文档.md`）：所有默认暴露工具返回统一包装为 `McpResponse`，成功为 `{"success": true, "data": ...}`，失败为 `{"success": false, "errorCode": "...", "errorMessage": "..."}`（错误码 `10004` 参数错误、`10003` 知识库无访问权限、`500` 系统内部错误）。对外默认提供两个工具：

- `kb_list_knowledge_bases()`：列出可见知识库，返回 `kbId`、`name`、`description`。当前映射两个知识库：`kbId=1` 保险绩优案例库、`kbId=2` 培训课程库。调用检索前应先用它确认可见的 `kbId`。
- `kb_search_knowledge(query, kbId, knowledgeTypes=None, retrievalMode="HYBRID", hybridWeights=None, topK=10)`：在指定 `kbId` 知识库内检索。MCP 侧不调用 chat/completions 做 query understanding；当前实现仅支持 `HYBRID`，`SPARSE` / `VECTOR` 暂返回参数错误 `10004`。本项目所有可返回内容以 `knowledgeType="SLICE"` 输出：`slice.content` 为截断预览、`slice.fullContent` 为全文、`slice.sliceType` 保留原业务类型（客户旅程 / 销售策略 / 场景话术 / 异议处理 / 培训课程）、`slice.citations` 为原文引用与定位（平台契约外的扩展字段）。`topK` 默认 10、最大 50。

默认 tool 暴露模式由 `MCP_TOOL_PROFILE` 控制，适合在服务器 `.env.mcp` 中调整后重启 MCP 容器：

- `MCP_TOOL_PROFILE=kb`：默认，只暴露 `kb_list_knowledge_bases` / `kb_search_knowledge`
- `MCP_TOOL_PROFILE=legacy`：只暴露旧 `search_knowledge` / `retrieval_status` / `list_filter_options`
- `MCP_TOOL_PROFILE=both`：新旧 tool 都暴露，适合灰度切换

旧 `search_knowledge`、`retrieval_status` 和 `list_filter_options` 的实现保留在代码中；`legacy` 或 `both` 模式会重新暴露它们。

项目根目录的 `.mcp.json` 已注册该服务，在本仓库中打开 Claude Code 即可直接使用。服务从项目目录的 `.env` 读取配置，MCP 检索只需要 embedding、rerank 与 Milvus 配置；对外错误消息经过白名单过滤，不泄漏内部路径与堆栈。lite 模式下索引是本地单进程文件，MCP 服务与 Web 服务不能同时打开同一个索引文件。

## Web 问答与文档入库界面

Web 界面同时提供知识问答和文档入库工作台。文档入库会在后台执行严格加工与原子写入，不需要先手工运行 CLI 的 parse/index 命令。

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

Docker 部署可在项目根目录运行：

```bash
docker compose up -d --build
```

默认打开 `http://localhost:8080`。无论采用哪种启动方式，都可在左侧顶层导航点击“文档入库”进入 Web 文档入库工作台。

原始数据文件应放在项目 `data/` 目录下。点击引用时，页面会展示 `source_path`、来源类型、locator、原文摘录和定位置信心。右侧“检索证据”会展示本次进入回答模型的 evidence chunks，方便对照回答是否被完整证据支撑。点击“在 Finder 中显示文件”只会打开 `data/` 目录内的本地文件；第一版不会尝试把 Word/PPT/PDF 精确跳转到段落或页内锚点。

### Web 文档入库

创建任务时先选择“案例知识库”或“课程知识库”，再上传一个 `docx / pptx / pdf / txt` 文件或一个 `zip` 压缩包。上传完成只会创建待确认任务；页面会先展示识别出的输入项、文档数与忽略项，点击“确认并开始”后才进入后台队列。

输入映射规则如下：

- 单文件：映射为一个输入项。目标为案例库时是一条案例；目标为课程库时是一门课程。
- 案例 ZIP：ZIP 根目录中的所有受支持文件合并为一个虚拟案例，案例名取 ZIP 文件名（不含扩展名）；每个 ZIP 一级子目录映射为一个独立案例，其更深层目录中的文档仍归属该一级目录。根目录与一级目录可以同时存在。
- 课程 ZIP：每个受支持文件独立映射为一门课程，并保留 ZIP 内相对路径；文件的相对父目录写入 `course_series`，根目录文件的 `course_series` 为空。因此目录只表示课程系列，不会把同一目录中的多个文件合并成一门课程。
- ZIP 中的目录条目、`__MACOSX`、隐藏路径、`~$` 临时文件及不支持格式会计入忽略项；如果最终没有受支持文档，预检失败。

任务固定展示“上传 → 解析 → 切分 → 入库”四阶段。案例的销售洞察抽取属于解析阶段；课程任务对任何受支持文件的解析/切分失败采用 fail-fast。课程 LLM 摘要、受众与销售环节标签属于可选增值：只有这类 enrichment 失败会降级为 warning，继续使用规则产物入库；其他解析、切分、embedding 或写库错误都会使整批失败。

入库遵循全有或全无语义：所有输入项先全部加工和校验，再生成整批 embedding；持有目标 collection 写锁后保存同 ID 旧记录快照并批量提交。提交前失败不会触碰知识库；提交中失败会删除本批新记录并恢复被覆盖的旧记录。补偿尚未完成时任务保持 `rolling_back`（rollback pending），系统保留恢复材料并自动退避重试，此时不能重试或删除；只有恢复完成后才会进入可重试的 `failed`，因此页面中的“任务未写入知识库”表示没有遗留本批变更。

失败任务从头重试：重试会先清空该任务所有 attempt 的解析、切分、staging 和回滚等中间产物，再从第一个输入项重新处理；原始上传文件位于独立的 `source/` 目录，会保留到删除任务为止。删除任务会删除任务历史、原始上传和工作目录，但删除任务不撤销已经成功写入知识库的内容；如需删除或重建知识本身，应另行执行索引维护。

任务 SQLite、原始上传及 attempt 工作目录默认持久化在 `.local/web_ingestion/`；Docker Compose 将宿主机 `./.local` 挂载到容器 `/app/.local`。迁移或备份时应连同该目录一起处理。

Web 入库按单机、单 API worker、单 writer 部署：进程内 Runner 串行执行任务；Web 与 CLI 对同一 Milvus URI 和 collection 的写操作还会使用 `.local/index-locks/` 下的文件锁。该锁只协调同一台机器且共享这一路径的进程，不是跨主机分布式锁；不要启动多个 API worker、多个主机副本或使用彼此独立的 `.local` 目录并发写同一 collection。

ZIP 会在预检和实际解压时重复校验，拒绝绝对路径、Windows 盘符、`..` 路径穿越、符号链接、加密或非常规条目、文件/目录路径冲突、重复覆盖、超过 512 字符的路径及异常压缩比。上传采用流式落盘，解压逐条目计数，默认安全限制如下：

| 环境变量 | 默认值 | 含义 |
| --- | ---: | --- |
| `WEB_INGEST_MAX_UPLOAD_BYTES` | `536870912`（512 MiB） | 单次上传文件上限 |
| `WEB_INGEST_MAX_ZIP_ENTRIES` | `2000` | ZIP 条目总数上限（包含目录和忽略项） |
| `WEB_INGEST_MAX_EXTRACTED_BYTES` | `2147483648`（2 GiB） | ZIP 解压后全部条目的总大小上限 |
| `WEB_INGEST_MAX_ENTRY_BYTES` | `536870912`（512 MiB） | 单个 ZIP 条目上限；单文件物化时也受此限制 |
| `WEB_INGEST_MAX_COMPRESSION_RATIO` | `100` | 单个 ZIP 条目的最大解压/压缩大小比 |

这五项配置必须是正数。应用层默认最多接收 512 MiB，Docker Web 前置 Nginx 同时配置了 `client_max_body_size 512m;`；如果提高应用上传上限，也必须同步调整 Nginx 限制，否则请求会先被代理拒绝。

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
MILVUS_COURSE_COLLECTION=xhbx_course_chunks
MILVUS_VECTOR_DIM=
```

`MILVUS_COLLECTION` 存放绩优案例知识；`MILVUS_COURSE_COLLECTION`（默认 `xhbx_course_chunks`）存放培训课程知识，检索时两库聚合召回。

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

Web 问答检索数量：

```env
WEB_RETRIEVAL_TOP_N=20
WEB_RETRIEVAL_TOP_K=5
```

- `WEB_RETRIEVAL_TOP_N`：Web 单次问答与批量执行的召回数量，默认 `20`，范围 `1–100`。
- `WEB_RETRIEVAL_TOP_K`：Web 单次问答与批量执行的引用数量，默认 `5`，范围 `1–20`，且不得大于 `WEB_RETRIEVAL_TOP_N`。

这两个值由后端读取并统一下发，知识问答页面不再提供手工输入控件；CLI、MCP 和 A2A 的显式检索参数不受影响。

`MILVUS_MODE` 默认为 `lite`，继续使用 `MILVUS_LITE_PATH` 指向的 Milvus Lite 本地文件。

如需连接本机 Docker Milvus：

```env
MILVUS_MODE=docker
MILVUS_URI=http://localhost:19530
MILVUS_TOKEN=
```

如果 Docker Milvus 启用了鉴权，把 `MILVUS_TOKEN` 设置为对应 token；未启用鉴权时留空。本项目只根据配置选择连接方式，不负责启动 Docker 服务。

`MILVUS_VECTOR_DIM` 可以留空。首次入库时会根据 embedding 返回向量长度自动创建 collection。
