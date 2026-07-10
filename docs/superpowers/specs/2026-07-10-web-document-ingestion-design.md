# Web 文档入库工作台设计

日期：2026-07-10

## 背景

`xhbx-rag` 已具备两条本地知识加工管线，但 Web 端目前只有问答与批量问答能力：

- 案例知识管线：`generate-insights → parse → index`，通过 LLM 抽取销售洞察后生成案例知识切片，写入案例 collection。
- 课程知识管线：`parse-course → index`，解析 `docx / pptx / pdf / txt`，按课程规则切分并写入课程 collection；课程级 LLM 摘要与标签失败时可降级为规则产物。
- Web 后端使用 FastAPI；批量问答已有 SQLite 持久化任务、后台 Runner、重启恢复与轮询 API，可复用其边界模式。
- Web 前端使用 React 19 + Vite，现有桌面布局是“左侧会话列表 / 中央工作区 / 右侧索引与证据明细”三栏结构。

本功能在 Web 中新增文档入库工作台，让用户上传单个文档或 ZIP，选择目标知识库，预检输入结构，启动持久化异步任务，并观察上传、解析、切分、入库全过程。

## 目标

- 支持上传单个 `docx / pptx / pdf / txt` 文件或 `.zip` 压缩包。
- 页面上明确选择“案例知识库”或“课程知识库”。
- 案例知识库执行完整管线：销售洞察 LLM 抽取、结构化解析、案例切分、向量化、入库。
- 课程知识库执行现有课程解析、课程切分、课程级摘要与标签、向量化、入库。
- 任务由 FastAPI 内置后台 Runner 异步执行，状态与历史写入 SQLite；刷新页面或重启服务后仍可查询。
- 采用全批成功语义：任一必需步骤失败，整批任务失败，目标 collection 保持任务开始前的状态。
- 失败任务可从头重试；重试保留原始上传文件，清理并重新生成其他全部产物。
- Web 展示预检结果、四阶段进度、输入项明细、警告、失败原因、attempt 与时间线。
- 安全处理 ZIP 路径、压缩炸弹、上传容量和文件名，错误只向前端暴露固定或经过归一化的安全中文信息。

## 非目标

- 第一版不支持 `rar / 7z`。
- 不支持音视频、图片、`xlsx`、旧版 `doc / ppt / wps` 或任意未知格式的 Web 入库。
- 不在 Web 暴露 `rebuild`；所有任务只执行增量 upsert。
- 不支持部分成功、跳过损坏的受支持文档后继续入库，或只重试某个输入项。
- 不支持取消运行中的任务。
- 不支持在 Web 中编辑 LLM prompt、切块阈值、案例名称或课程名称。
- 不引入 Celery、RQ、Redis 或独立 Worker 服务。
- 不承诺多 FastAPI 实例的并行任务调度；第一版是单实例部署。
- 不做病毒扫描和内容合规审查；上传来源仍由部署方控制。
- 不改变现有 CLI 的业务语义；Web 编排复用底层 Python 函数，不启动 CLI 子进程。

## 已确认的产品决策

1. 页面上选择案例知识库或课程知识库。
2. 案例知识库始终走完整案例管线，不把普通文档直接切块写入案例 collection。
3. 第一版压缩包只支持 ZIP。
4. 任务在后端持久化异步执行；刷新页面或关闭浏览器不会停止任务。
5. 任一必需步骤失败，整批失败且不得在知识库残留本批数据。
6. 重试从头开始，清理提取、生成、解析、切分、向量与回滚文件；不复用已成功的中间产物。
7. 原始上传文件不是中间产物，保留到用户删除任务，以便失败任务无需重新上传即可重试。
8. 课程级 LLM 摘要与标签属于可选增值；该步骤失败只产生警告，不触发整批失败。
9. 采用现有风格的三栏工作台，不使用侧滑上传抽屉或独立的孤立页面。

## 输入识别规则

### 单文件

- 上传到案例知识库：创建一个合成案例目录，文件是该案例的唯一素材；案例名取去掉扩展名后的文件名。
- 上传到课程知识库：该文件独立视为一门课程；课程名取去掉扩展名后的文件名，课程体系为空。

### ZIP 上传到案例知识库

- ZIP 的每个一级子目录只要递归包含至少一个受支持文档，就识别为一个案例。
- 一级子目录下的更深目录保持现有章节组织语义，由案例素材加载器递归处理。
- ZIP 根目录下的所有受支持文档合并为一个案例，案例名取 ZIP 文件名去掉扩展名后的值。
- 只包含被忽略文件或不支持格式的一级目录不生成案例。
- 根目录案例名与一级目录案例名冲突，或两个名称经安全文件名归一后发生冲突时，预检失败；第一版不在页面内改名。
- ZIP 没有识别出任何案例时，预检失败。

### ZIP 上传到课程知识库

- ZIP 内每个受支持文档独立视为一门课程。
- 文档在 ZIP 内的父目录相对路径写入 `course_series`；文件名去掉扩展名后作为 `course_name`。
- ZIP 没有识别出任何课程文档时，预检失败。

### 被忽略的条目

以下条目在预检结果中计数并可展开查看，但不参与任务，也不导致失败：

- 目录条目。
- `__MACOSX` 下的条目。
- 任意路径段以 `.` 开头的隐藏条目。
- 文件名以 `~$` 开头的 Office 临时文件。
- 扩展名不在 `docx / pptx / pdf / txt` 白名单内的普通文件。

受支持文档在实际解析、切分或必需加工步骤中失败时，必须触发整批失败。

## 页面与导航设计

### 顶层导航

- 左栏顶部增加两个顶层入口：“知识问答”和“文档入库”，使用同一套 Lucide 线性图标与文字标签。
- 默认地址 `/` 保持现有问答界面。
- 入库工作台使用 `/?view=ingestion`；选中历史任务时使用 `/?view=ingestion&job=<job_id>`。
- 使用 History API 与 `popstate` 同步 URL，不为两个顶层视图引入完整路由框架。
- 浏览器返回、前进和刷新必须恢复当前工作台与选中任务。

### 三栏结构

入库工作台继续使用现有三栏布局：

- 左栏：顶层导航、“新建任务”主操作、按创建时间倒序的入库任务历史。
- 中栏：新建任务的上传/预检/确认流程，或所选任务的阶段进度、输入项与失败恢复界面。
- 右栏：任务摘要、目标知识库、attempt、统计、时间线、警告与安全错误信息。

任务列表状态必须同时使用图标和中文文字，不得只用颜色区分。状态文案为“待确认、排队中、运行中、清理中、已完成、失败、删除中”。成功但存在课程增值警告时显示“已完成 · 有警告”。

### 新建任务流程

1. 用户先选择目标知识库，再拖放或通过文件选择器选择单文件/ZIP。
2. 前端使用 `XMLHttpRequest` 上传，以便展示真实上传百分比；同时提供可访问的 `<input type="file">` 作为拖放替代。
3. 上传接口原子保存源文件并执行只读预检，返回 `draft` 任务与识别结果。
4. 中栏显示目标知识库、源文件、案例/课程清单、文档数量和被忽略条目。
5. 用户点击“确认并开始”，任务从 `draft` 原子切换为 `queued` 并进入 Runner 队列。
6. 若用户离开未确认的 draft，任务保留在历史中，稍后可继续确认或删除。

第一版不允许在 draft 创建后修改目标知识库；若选错，删除 draft 并重新上传。这样避免对同一上传源维护两套含义不同的预检结果。

### 运行与失败界面

中栏固定显示四个用户阶段：

1. 上传：源文件已安全保存并完成预检。
2. 解析：案例的销售洞察抽取与结构化解析，或课程文档解析与可选增值。
3. 切分：案例或课程 chunk 构建、合并和完整性校验。
4. 入库：embedding、旧记录快照、批量 upsert、flush 与提交确认。

后端可保存更细的内部事件，但前端必须映射到以上四个稳定阶段。运行中显示当前输入项、已完成项数、总项数、chunk 数和最近事件。轮询进度间隔默认 2 秒；左栏列表在存在 `queued / running / rolling_back` 任务时每 5 秒刷新，全部终态后停止轮询。

失败界面必须：

- 明确显示“任务未写入知识库”。
- 显示首个失败输入项、失败阶段和安全错误原因。
- 显示已经完成但最终被丢弃的输入项，以及尚未执行的输入项。
- 在状态为 `failed` 时提供“从头重试”。
- 在 `rolling_back` 时显示“正在恢复知识库，请勿重试或删除”，且禁用相关操作。

### 删除任务

- 仅 `draft / succeeded / failed` 可发起删除；`queued / running / rolling_back` 返回 409。
- 删除前显示二次确认，明确说明会删除原始上传文件和任务历史，但不会删除已经成功入库的知识。
- 删除操作先把任务原子切换为内部 `deleting` 状态，再清理任务目录、attempt、输入项、事件与任务主记录；重复 DELETE 和重启恢复都继续同一个幂等清理流程。
- 删除成功任务不回滚已经提交的知识；“删除任务”和“撤销入库”是不同能力，后者不在第一版范围内。

### 响应式与可访问性

- `>= 1180px` 使用完整三栏。
- `768–1179px` 使用左栏 + 中栏；右栏任务摘要按正常文档流放到中栏阶段内容之后，不使用遮挡主流程的抽屉。
- `< 768px` 顶层导航、可折叠任务列表、中栏内容和任务摘要纵向堆叠，禁止横向滚动。
- 在 375、768、1024、1440 px 验证布局。
- 所有按钮和文件输入可通过键盘操作；焦点样式沿用现有可见轮廓。
- 图标按钮必须有 `aria-label`；上传错误使用 `role="alert"`，阶段与进度更新使用 `aria-live="polite"`。
- 可点击目标最小 44×44 px；文字与背景对比度至少 4.5:1。
- 动效只使用 150–300 ms 的颜色、阴影和透明度过渡，并遵循 `prefers-reduced-motion`。

## 后端架构

### 组件边界

#### `IngestionStore`

独立 SQLite 存储，职责包括：

- 创建 draft 任务并原子保存预检输入项。
- 维护任务、attempt、输入项、阶段、警告、错误、统计与事件。
- 通过条件更新领取 queued 任务，阻止重复执行。
- 校验 start、retry、delete 的状态转换。
- 在服务启动时识别未完成任务并返回恢复动作。

默认数据库路径为 `.local/web_ingestion/ingestion.sqlite3`，使用 WAL、参数化 SQL 和 `BEGIN IMMEDIATE`，与批量问答数据库隔离。

#### `UploadService`

职责包括：

- 以流式方式把上传内容写入同目录临时文件，完成大小校验后原子重命名。
- 安全归一文件名，并把用户文件限制在当前任务目录。
- 读取 ZIP 中央目录完成预检，不在请求线程中展开文档内容。
- 校验 ZIP 安全规则并生成稳定的输入单元清单。
- 执行任务时安全解压到 attempt 工作目录。
- 在失败、重试和删除时按生命周期清理文件。

#### `IngestionRunner`

- 单 daemon Worker 线程跨任务串行执行，复用现有 `queue.Queue` 和生命周期模式。
- 服务启动时重新入队 `queued` 任务。
- 同一时间只执行一个 Web 入库任务，避免 LLM、embedding 和 Milvus 写入同时争用。
- 案例内部仍可使用现有 `section_concurrency`；不增加跨案例并行，确保首个失败后可立即停止整批。
- 直接调用 Python 服务，不解析 CLI stdout，也不启动子进程。
- 每个阶段与输入项通过 Store 接口持久化进度，异常经统一安全错误映射后落库。

#### `AtomicIndexer`

独立于 CLI `index_chunks` 的事务式批量索引编排，职责包括：

- 在接触 Milvus 前加载并校验全部 chunk，生成全部 embedding。
- 获取目标 collection 的写锁。
- 记录 collection 是否原本存在、本批 chunk ID，以及同 ID 旧记录的完整回滚快照。
- 持久化 commit journal 后执行单次批量 upsert 和 flush。
- 异常或进程重启时删除本批 ID，恢复旧记录；若本任务创建了原本不存在的 collection，则回滚时删除该新 collection。
- 只有补偿完成后，任务才能进入 `failed`。

`MilvusStore` 需要新增按 ID 删除和读取含 vector 的完整原始行能力；回滚恢复必须使用原始行，不重新调用 embedding。

#### `IngestionRoutes`

FastAPI 路由只负责请求校验、依赖获取、状态冲突映射和安全响应，不在路由内执行长任务。Store 与 Runner 通过 `app.state` 注入，lifespan 中启动、恢复和停止 Runner，测试可注入 fake。

### 目录布局

每个任务使用独立目录：

```text
.local/web_ingestion/jobs/<job_id>/
├── source/
│   └── <safe_original_name>       # 原始上传文件，保留到删除任务
└── attempts/
    └── <attempt_no>/
        ├── extracted/             # ZIP 解压或单文件合成输入目录
        ├── generated/             # 案例 LLM 产物
        ├── parsed/                # 结构化结果与 chunks
        ├── staging/               # 合并后的全批 chunks 与校验清单
        └── rollback/              # commit journal 与旧记录快照
```

任务成功后删除当前 attempt 下的 `extracted / generated / parsed / staging / rollback`，保留源文件和 SQLite 历史。任务失败时，在确认无需回滚或回滚成功后执行相同清理。`rolling_back` 期间保留 rollback 目录，直到补偿成功。

## 数据模型与状态机

### SQLite 表

#### `ingestion_jobs`

- `job_id`：32 位十六进制 UUID，主键。
- `source_name`：安全展示名。
- `source_kind`：`file | zip`。
- `source_path`：任务目录内的绝对源文件路径，仅后端返回前不暴露。
- `target`：`case | course`。
- `status`：`draft | queued | running | rolling_back | succeeded | failed | deleting`。
- `current_stage`：`uploaded | parsing | chunking | indexing | completed`。
- `attempt_count`、`item_total`、`item_done`、`document_total`、`chunk_total`、`warning_count`。
- `error_code`、`error_detail`：仅保存已归一化安全错误。
- `created_at`、`updated_at`、`started_at`、`finished_at`：带 UTC 时区 ISO8601。

#### `ingestion_items`

- `(job_id, item_index)` 复合主键。
- `unit_key`：预检生成的稳定输入键；案例为 `__root__` 或一级目录名，课程为文档相对路径。
- `display_name`、`relative_paths_json`、`document_count`。
- `status`：`pending | running | succeeded | failed | skipped`。
- `current_stage`、`chunk_count`、`warning_count`、`error_detail`、`updated_at`。

预检时一次性写入所有输入项；retry 重置状态但不改变输入项定义。

#### `ingestion_attempts`

- `(job_id, attempt_no)` 复合主键。
- `status`、`current_stage`、`commit_state`。
- `workspace_path`、`journal_path`。
- `error_code`、`error_detail`、`started_at`、`finished_at`。

`commit_state` 为 `not_started | prepared | committed | rolling_back | rolled_back`，用于进程重启后决定是否必须补偿。

#### `ingestion_events`

- `(job_id, attempt_no, sequence)` 复合主键。
- `event_type`、`message`、`payload_json`、`created_at`。

事件只存安全、体积受控的摘要；不保存完整模型 prompt、模型原始响应、密钥或文档正文。每个 attempt 最多保留 2000 条事件，超过后合并高频细粒度进度事件。

### 状态转换

```text
draft --start--> queued --claim--> running --commit success--> succeeded
                              |
                              +--failure before commit-----------------> failed
                              |
                              +--failure after journal--> rolling_back --rollback success--> failed

failed --retry--> queued   # attempt_no + 1，全部中间产物从头生成
draft/succeeded/failed --delete--> deleting --cleanup success--> removed
```

非法转换返回 409，不能静默忽略。

### 服务重启恢复

- `draft`：保留原样，等待用户确认或删除。
- `queued`：重新入队。
- `running` 且 `commit_state=not_started`：清理 attempt 工作目录，标记 `failed`，错误为“服务重启导致任务中断，请从头重试”。
- `running / rolling_back` 且 `commit_state` 为 `prepared / rolling_back`：先进入或保持 `rolling_back`，依据持久化 journal 完成补偿；成功后标记 `failed`。
- 回滚期间 Milvus 暂不可用时保持 `rolling_back`，Runner 以 2 秒为初始间隔指数退避，单次间隔上限 60 秒，并持续重试直到补偿成功或服务关闭；该状态禁止 retry 和 delete，避免丢失恢复依据。
- `commit_state=committed` 但任务主记录未更新时，恢复过程核对 journal 后把任务标记 `succeeded`，避免对已经确认提交的批次误回滚。
- `deleting`：继续执行幂等目录与 SQLite 子记录清理，成功后删除任务主记录。

## 管线编排

### 案例知识库

对每个预检案例按 `item_index` 串行处理：

1. 在 attempt 工作目录构造独立案例素材目录。
2. 调用现有 `generate_case_sales_insights_async` 完成章节证据抽取和案例归纳。
3. 只有生成状态为 `ok` 才继续；Web 全批语义不接受现有 CLI 的 `partial` 作为成功。
4. 调用 `parse_inputs → normalize_case → build_chunks`。
5. 将该案例 chunk 写入 staging，并校验 chunk 非空、ID 唯一、文本可编码为 UTF-8、引用结构合法。
6. 任一案例失败后停止后续案例，整批进入失败清理；已完成案例 staging 产物不得入库。

为避免 ZIP 根目录案例与一级目录案例的输出目录互相覆盖，每个输入项使用 `item_index + unit_key hash` 的工作目录；业务 `case_name` 仍按输入识别规则生成。

### 课程知识库

1. 安全解压或构造单文件课程根目录。
2. 逐课程文件调用现有解析与 `build_course_chunks` 能力，保留相对路径作为 `course_series`。
3. 默认启用课程级摘要与标签；其失败记录 warning，继续使用规则产物。
4. 文档解析、规则切分、chunk 校验失败属于必需步骤失败，立即停止后续课程并使整批失败。
5. 全部课程成功后把 chunks 合并到 staging，验证 ID 唯一且非空。

现有 `parse_course_dir` 需要提供严格 Web 模式或拆出单文件服务接口：可选增值失败仍为 warning，但任何受支持文件的解析/切分失败必须抛出异常，不能只写入报告后让其他文件继续形成可入库结果。

### 全批校验

进入入库阶段前必须满足：

- 每个预检输入项均为成功状态。
- staging chunk 数大于 0。
- 全批 `chunk_id` 唯一。
- 每个 chunk 通过 `RagChunk` 数据模型校验，文本非空且 UTF-8 可编码。
- 案例任务只包含案例 chunk 类型；课程任务只包含 `training_course`。
- 所有 citations 和 metadata 可序列化，字段长度不会超过 Milvus schema 限制。

## 原子式增量入库

Milvus 不提供与 SQLite 等价的跨请求事务，因此采用持久化提交日志与补偿恢复实现任务级全有或全无语义。

### 提交前

1. 加载全批 staging chunks。
2. 一次性生成全部 embedding；向量数量和维度必须与 chunk 完全一致。
3. 获取按 Milvus URI + collection 名称派生的 collection 写锁。当前 Web Runner 自身串行；该锁还应接入项目内 CLI 索引入口，避免同一部署中的 CLI 与 Web 同时写同一 collection。
4. 查询 collection 是否存在。
5. 获取所有待写 `chunk_id` 对应的旧记录，必须包含 vector 与全部 schema 字段。
6. 原子写入本地 rollback snapshot 和 commit journal，fsync 后把 `commit_state` 更新为 `prepared`。

### 提交

1. collection 不存在时按当前 embedding 维度创建。
2. 对全部记录执行一次 `upsert`，随后 `flush`。
3. 可按 ID 读取并校验写入数量；校验成功后把 journal 和 SQLite `commit_state` 更新为 `committed`。
4. 标记任务 `succeeded`，释放写锁，再删除回滚和其他中间产物。

### 补偿

只要 `prepared` 后未确认 `committed`，异常处理或重启恢复必须：

1. 获取同一 collection 写锁。
2. 若 collection 在任务前不存在，则删除该任务创建的 collection。
3. 若 collection 原本存在，则按本批全部 chunk ID 删除可能写入的新记录，再以快照中的原始行恢复同 ID 旧记录并 flush。
4. 校验新 ID 不存在、旧 ID 与快照数量一致。
5. 把 `commit_state` 更新为 `rolled_back`，任务更新为 `failed`，然后清理中间产物。

若补偿本身失败，任务保持 `rolling_back`，不得宣称失败清理完成，也不得允许从头重试。

### 并发边界

- 单 FastAPI 实例内由单 Runner 和 collection 写锁保证顺序。
- 项目内 CLI `index` 与 Web AtomicIndexer 使用同一文件锁协议。
- 多主机共享远程 Milvus 的分布式锁不在第一版范围内；生产部署必须保持单写实例。
- 不受该锁协议约束的外部客户端并发写入同一 chunk ID 会破坏补偿前提，属于部署约束并在运维文档中明确。

## API 设计

所有响应错误使用 `{ "detail": "安全中文信息" }`；服务器日志记录带 `job_id / attempt_no / stage` 的完整异常。

### `POST /api/ingestion-jobs`

请求为 `multipart/form-data`：

- `file`：必填，只允许单个文件。
- `target`：必填，`case | course`。

行为：流式保存、校验大小和扩展名、ZIP 预检、创建 draft 任务。成功返回 HTTP 201：

```json
{
  "job_id": "2d4f...",
  "status": "draft",
  "target": "case",
  "source_name": "优秀案例.zip",
  "source_kind": "zip",
  "item_total": 3,
  "document_total": 12,
  "ignored_total": 4,
  "items": [
    {
      "item_index": 1,
      "unit_key": "王女士年金险案例",
      "display_name": "王女士年金险案例",
      "document_count": 4
    }
  ],
  "ignored_entries": ["__MACOSX/._说明.txt"],
  "created_at": "2026-07-10T08:00:00+00:00",
  "updated_at": "2026-07-10T08:00:00+00:00"
}
```

容量超限返回 413；格式、ZIP 安全或预检失败返回 400。失败创建不得留下任务记录或半文件，临时上传必须清理。

### `POST /api/ingestion-jobs/{job_id}/start`

- 仅 `draft` 可调用。
- Store 先在事务中切换到 `queued`，提交后再 `runner.enqueue(job_id)`。
- 返回 `{ "ok": true, "job_id": "...", "status": "queued" }`。

### `GET /api/ingestion-jobs`

- 默认最多返回最近 200 条任务摘要。
- 响应为 `{ "jobs": [...] }`，不包含输入项、事件和源文件绝对路径。

### `GET /api/ingestion-jobs/{job_id}`

- 返回任务主信息、预检项、当前 attempt、警告、错误和最近 200 条事件。
- 不返回文档正文、模型原始响应、文件系统绝对路径或回滚快照。

### `GET /api/ingestion-jobs/{job_id}/progress`

返回轮询所需轻量快照：

```json
{
  "job_id": "2d4f...",
  "status": "running",
  "current_stage": "parsing",
  "attempt_no": 1,
  "item_total": 3,
  "item_done": 1,
  "document_total": 12,
  "chunk_total": 28,
  "warning_count": 0,
  "active_item_index": 2,
  "message": "正在解析：李先生养老规划",
  "updated_at": "2026-07-10T08:08:00+00:00"
}
```

### `POST /api/ingestion-jobs/{job_id}/retry`

- 仅 `failed` 可调用。
- 事务内递增 `attempt_count`，重置任务和输入项状态，创建新 attempt 并切到 `queued`。
- 入队前删除除 `source/` 外的全部旧 attempt 工作目录；若清理失败，保持 `failed` 并返回 500，不得启动新 attempt。
- 返回 `{ "ok": true, "job_id": "...", "attempt_no": 2, "status": "queued" }`。

### `DELETE /api/ingestion-jobs/{job_id}`

- `draft / succeeded / failed / deleting` 可调用；其他状态返回 409。
- 首次调用先把任务事务性切换为 `deleting`，再执行幂等文件清理，最后删除 SQLite 子记录与任务主记录，避免列表看到已删记录但文件尚未清理。
- 文件清理失败返回 500 并保持 `deleting`；再次 DELETE 或服务重启恢复会继续清理。

## 上传与 ZIP 安全

### 默认限制

- 单次上传最大压缩/原始字节：`536870912`（512 MiB）。
- ZIP 条目数最大值：`2000`。
- ZIP 声明的解压后总字节最大值：`2147483648`（2 GiB）。
- 单个 ZIP 条目解压后最大值：`536870912`（512 MiB）。
- 单条目最大压缩比：`100:1`；空压缩内容按特殊规则处理，不能造成除零。
- ZIP 内归一化相对路径最大长度：`512` 个字符。

对应配置项：

- `WEB_INGEST_MAX_UPLOAD_BYTES`
- `WEB_INGEST_MAX_ZIP_ENTRIES`
- `WEB_INGEST_MAX_EXTRACTED_BYTES`
- `WEB_INGEST_MAX_ENTRY_BYTES`
- `WEB_INGEST_MAX_COMPRESSION_RATIO`

FastAPI 必须在读取流时执行字节计数，不能只信任 `Content-Length`。Nginx `client_max_body_size` 设为 `512m`，与默认应用上限一致；若部署方降低应用上限无需改 Nginx，若提高上限必须同步修改反向代理配置。

### ZIP 校验

- 拒绝绝对路径、Windows 盘符路径、任何归一化后包含 `..` 的路径。
- 拒绝符号链接和其他非常规 Unix 文件类型。
- 拒绝加密条目。
- 拒绝两个原始名称归一化到同一目标路径的重复覆盖。
- 拒绝条目数、单文件大小、总大小、压缩比或路径长度超限。
- 实际解压时再次执行累计字节计数，并验证每个目标路径仍位于 attempt 的 `extracted/` 根目录下。
- 上传文件名只用于生成安全展示名；服务器路径由 `job_id` 与安全文件名组成，不能接受客户端路径。

## 错误处理与可观测性

### 错误分类

- `upload_invalid`：扩展名、ZIP 结构或输入映射无效。
- `upload_too_large`：上传或解压限制超出。
- `parse_failed`：受支持文档无法解析，或案例 LLM 生成不是完整 `ok`。
- `chunk_failed`：切分、数据模型或全批校验失败。
- `embedding_failed`：embedding 调用或维度校验失败。
- `index_failed`：Milvus 创建、写入、flush 或写后校验失败。
- `rollback_pending`：补偿尚未完成，前端显示清理中而非普通失败。
- `service_restarted`：非提交阶段被服务重启中断。
- `storage_unavailable`：SQLite 或任务文件存储不可用。

前端只展示安全中文 detail。解析器、模型客户端和 Milvus 的原始异常记录到服务日志，不直接回传堆栈、URL、token、绝对路径或文档内容。

### 事件与日志

- 日志字段统一包含 `job_id`、`attempt_no`、`target`、`stage`、`item_index`。
- Runner 在上传完成、预检完成、attempt 开始、输入项开始/完成、阶段完成、commit prepared、commit committed、rollback 开始/完成、任务终态时写事件。
- 现有 trace sink 可继续记录底层案例/课程步骤，但写入 Web 事件前必须转为安全摘要。
- 指标至少包括任务数量、成功/失败数量、阶段耗时、输入项数量、chunk 数、warning 数和 rollback 次数。

## 配置与部署

- `pyproject.toml` 增加 FastAPI multipart 所需的 `python-multipart` 依赖。
- Docker API 容器继续挂载 `.local` 持久化目录，保证 SQLite、原始上传和 rollback journal 在容器重启后存在。
- `web/nginx.conf` 增加 `client_max_body_size 512m`。
- README 增加 Web 入库使用说明、ZIP 目录约定、安全限制、任务目录、重试语义和单写实例约束。
- `.env.example` 增加上传限制配置及默认值；这些配置有默认值，不加入 Web 状态接口的必填配置列表。

## 测试策略

### 后端单元测试

- 单文件案例/课程预检映射。
- ZIP 根文件、一级案例目录、嵌套章节和课程相对路径映射。
- 隐藏文件、`__MACOSX`、`~$` 与未知扩展名忽略。
- 案例名称冲突、空 ZIP、无受支持文档失败。
- ZIP 绝对路径、`..` 穿越、Windows 盘符、符号链接、加密、重复覆盖、条目数、单项大小、总大小和压缩比限制。
- 流式上传超过限制时清理临时文件且不创建任务。
- Store 的全部合法与非法状态转换、并发 claim、retry 重置、delete 冲突和 restart recovery。
- Runner 在首个必需输入项失败后停止后续输入，并且不调用 AtomicIndexer。
- 课程增值失败产生 warning，但课程解析失败使整批失败。
- retry 保留 source，删除所有旧 attempt 产物，并从第一个输入项开始。

### 原子索引测试

- embedding 失败时 Store 未被写入。
- 新 chunk ID 写入失败时删除所有本批新 ID。
- 覆盖已有 chunk ID 后写入失败时恢复旧 text、metadata、citations 和 vector。
- 原 collection 不存在且写入失败时删除任务创建的 collection。
- commit journal 写入失败时不执行 upsert。
- upsert 成功但任务状态写入前重启时，根据 committed journal 恢复为 succeeded。
- prepared 或 rolling_back 状态重启时完成补偿后进入 failed。
- rollback 失败时保持 rolling_back，禁止 retry/delete；恢复 Milvus 后可继续补偿。
- 同一 collection 的 CLI 与 Web 写锁互斥。

### API 测试

- multipart 创建 draft 的 201 响应及输入项结构。
- 400、404、409、413 与 500 的固定安全中文错误。
- start 先提交 SQLite 再入队。
- list/detail/progress 不泄漏绝对路径、正文、模型原始响应或回滚数据。
- retry、delete 状态约束与文件清理失败补偿。
- app lifespan 启动 Runner、恢复 queued/rolling_back 并在关闭时停止线程。

### 前端测试

- URL 与“知识问答/文档入库”顶层视图同步，浏览器前进后退恢复选择。
- 拖放和文件选择器的类型校验、XHR 上传进度和错误提示。
- 预检案例/课程清单、忽略项、确认开始。
- 左栏任务历史、终态停止轮询、过期请求不覆盖新状态。
- 四阶段进度、运行输入项、warning、失败原因、rolling_back 禁用和从头重试。
- 删除确认及删除成功后的选中任务回退。
- 键盘操作、可见焦点、`role="alert"`、`aria-live`、文字状态与图标状态。

### 集成与人工验收

1. 单个 `txt/docx/pptx/pdf` 分别进入案例库与课程库。
2. ZIP 多案例和 ZIP 多课程完整成功，刷新页面后仍可看到历史与统计。
3. ZIP 中一个受支持文档损坏，任务失败，目标 collection 的实体与旧记录内容不变。
4. 案例 LLM 抽取失败，整批不进入 embedding 或 Milvus。
5. 课程 LLM 增值失败，任务成功并显示 warning。
6. embedding、upsert、flush、写后校验分别注入故障，完成补偿后任务才显示失败。
7. 入库中途重启服务，恢复进程完成 commit 确认或 rollback，之后允许从头重试。
8. 失败任务重试后 attempt 增加，从头处理，旧中间产物不存在。
9. 在 375、768、1024、1440 px 无横向溢出，键盘可完成上传、确认、重试和删除。

## 验收标准

- 用户可以在 Web 中上传一个支持文档或 ZIP，选择目标知识库并在预检后启动任务。
- 案例任务执行完整销售洞察管线；课程任务执行现有课程管线。
- 任务历史和状态在浏览器刷新与服务重启后保留。
- UI 清楚展示上传、解析、切分、入库四阶段以及当前输入项、计数、warning 和安全错误。
- 任一必需步骤失败时，最终状态不是部分成功，目标 collection 不含本批新增残留，被覆盖的旧记录保持原值。
- rollback 未完成时任务显示清理中，不允许重试或删除。
- 失败任务从头重试并清理所有非 source 中间产物。
- ZIP 安全测试、Store/Runner 状态机测试、AtomicIndexer 故障恢复测试、API 测试和前端交互测试全部通过。
- 现有问答、批量问答、CLI 案例入库、CLI 课程入库和检索测试无回归。

## 采用与未采用的方案

### 采用：FastAPI 内置任务服务

复用项目已有的 SQLite + Runner 模式，底层直接调用 Python 管线。它与当前单机 Docker 部署一致，进度、状态、重启恢复和故障注入测试都能使用明确接口实现。

### 未采用：CLI 子进程

虽然可复用命令入口，但通过 stdout 推断阶段、结构化错误、重启恢复和原子补偿都不可靠，且会重复一层参数与进程生命周期管理。

### 未采用：外部任务队列

Celery/RQ 可支持多 Worker，但需要 Redis 和新的部署面；当前只需单实例持久化任务，增加外部队列不符合第一版范围。

### 未采用：侧滑抽屉或临时弹窗

入库任务有历史、长耗时、失败恢复和大量输入项明细，临时层会压缩信息并破坏路径连续性。三栏工作台更符合现有产品结构。

## 规格自检结论

- 规格没有未定义的占位符或悬而未决的阈值；上传与 ZIP 限制均给出确定默认值。
- “整批失败”与“课程增值可降级”边界已明确：只有可选课程增值失败是 warning，其他必需阶段失败均触发整批失败或回滚。
- 删除任务不会撤销成功入库，避免把任务生命周期与知识删除混为一谈。
- `rolling_back` 是可见的非终态，避免回滚未完成时错误宣称知识库无残留。
- 第一版范围限定为单 FastAPI 写实例；分布式锁、外部任务队列、取消与撤销入库均明确排除。
