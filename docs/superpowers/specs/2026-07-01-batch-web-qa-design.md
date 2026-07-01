# Web 批量问答界面设计

日期：2026-07-01

## 背景

当前 Web 界面已经支持单条问题的流式 RAG 问答、执行步骤展示、引用与检索证据展示，以及 bad case 反馈落盘。业务人员希望提供一个包含多个问题的文档，上传后系统批量调用 RAG 生成回答，并能看到每个问题的执行状态，同时对回答不佳的条目提交 bad case 反馈。

首版选择前端编排批量执行：前端解析 `.txt` 或 `.csv` 文件得到问题列表，然后串行复用现有 `/api/answer/stream` 接口。这样能最大化复用当前稳定链路，避免新增后端任务系统、任务持久化和恢复语义。

## 目标

- 在现有 Web 界面新增“批量跑问题”工作模式。
- 支持上传 `.txt` 和 `.csv` 文件，也支持粘贴多行问题。
- 将批量输入解析成问题列表，并允许用户在执行前检查数量和内容。
- 串行执行每个问题，避免同时压垮本地 Milvus、embedding、rerank 和回答模型服务。
- 每个问题展示独立执行状态：等待中、运行中、成功、失败。
- 运行中的问题展示现有 RAG trace 步骤和增量回答。
- 成功的问题展示回答、引用、检索证据，并复用现有 bad case 反馈能力。
- 失败的问题保留错误摘要，并支持单条重试。
- 保持现有单问会话、引用详情、Finder reveal、bad case API 和本地会话持久化行为不变。

## 非目标

- 首版不支持 `.docx`、`.xlsx`、`.pdf` 直接解析。
- 首版不新增后端 batch job、任务恢复、队列持久化或跨页面继续执行。
- 首版不做批量结果导出文件。
- 首版不把批量执行结果写入现有聊天会话历史。
- 首版不改变 RAG 核心检索和回答链路。

## 推荐方案

采用前端编排批量执行。

这个方案的取舍：

- 优点：复用现有 `/api/answer/stream`、执行步骤展示和 bad case 反馈结构；后端改动少；每条问题都有实时中间状态；失败隔离清楚。
- 缺点：刷新页面会丢失未完成批次；浏览器关闭后不能后台继续跑；首版不能统一导出整批结果。

未采用的方案：

- 后端同步批量接口：实现简单，但长请求容易超时，且中间状态展示弱。
- 后端任务队列加轮询或 SSE：能力完整，但需要任务存储、取消、恢复、并发控制和清理策略，超出首版范围。

## 输入格式

`.txt`：

- 按换行切分。
- 去掉首尾空白。
- 空行忽略。
- 每个非空行作为一个问题。

`.csv`：

- 使用浏览器端解析逻辑，支持逗号、双引号包裹字段和换行字段。
- 如果第一行包含 `question` 表头，读取 `question` 列。
- 如果没有 `question` 表头，读取第一列。
- 去掉首尾空白。
- 空问题忽略。

粘贴输入：

- 与 `.txt` 一致，按非空行解析。

校验规则：

- 解析后至少需要 1 个问题。
- 单批最多 100 个问题，避免误上传超大文件导致长时间占用模型服务。
- 单个问题长度沿用后端 `AnswerRequest.query` 的非空约束；前端只做非空校验，具体长度和安全错误仍由后端边界兜底。
- 文件扩展名只接受 `.txt` 和 `.csv`。

## UI 设计

现有页面保持三栏结构：左侧会话列表，中间问答主区域，右侧索引和溯源面板。批量能力放在中间问答主区域顶部，通过分段控件在“单问”和“批量”之间切换。

批量模式包含：

- 文件上传按钮，接受 `.txt,.csv`。
- 粘贴输入区，用于直接输入多行问题。
- 解析结果摘要：问题数量、来源类型、校验错误。
- 批量参数沿用当前 `topN` 和 `topK` 控件。
- 执行按钮：开始批量运行。
- 清空批次按钮：清空当前批次结果。
- 批量结果列表：每行展示序号、问题、状态、当前步骤摘要、回答预览和操作。

结果项交互：

- 等待中：显示“等待中”。
- 运行中：显示 `ProcessTimeline` 和增量回答。
- 成功：显示完整回答、引用列表、bad case 反馈面板。
- 失败：显示安全错误摘要和“重试”按钮。
- 点击某条成功结果中的引用时，复用右侧溯源详情面板展示该 citation。
- 点击某条结果的“查看证据”时，右侧检索证据面板显示该条回答的 `retrieval_evidences`。

为减少重复代码，单问和批量结果共享以下组件或辅助函数：

- `ProcessTimeline`
- `CitationList`
- `BadCasePanel`
- `EvidenceList`
- `validateLimits`
- `formatProcessPayload`

## 前端状态模型

新增批量类型：

```ts
type BatchQuestionStatus = "pending" | "running" | "succeeded" | "failed";

type BatchQuestion = {
  id: string;
  query: string;
  top_n: number;
  top_k: number;
  status: BatchQuestionStatus;
  process_steps: AnswerProcessStep[];
  streaming_answer: string;
  response?: AnswerResponse;
  error?: string;
};

type BatchRunState = {
  source_label: string;
  questions: BatchQuestion[];
  running: boolean;
  active_question_id?: string;
};
```

状态更新规则：

- 上传或粘贴解析成功后生成 `pending` 列表。
- 点击开始后将 `running=true`，按数组顺序逐条执行。
- 每条执行前设置为 `running`，并记录该条提交时的 `top_n/top_k`。
- SSE `step` 事件追加到当前条目的 `process_steps`。
- SSE `answer_delta` 追加到当前条目的 `streaming_answer`。
- SSE `final` 将当前条目标记为 `succeeded` 并保存 `response`。
- 捕获异常时将当前条目标记为 `failed` 并保存 `error`。
- 当前条结束后继续下一条；整批完成后 `running=false`。
- 单条重试只重跑该条，不影响其他成功结果。

首版不把 `BatchRunState` 写入 `localStorage`，避免把大量回答、引用和检索证据写入浏览器存储。

## 数据流

1. 用户切换到“批量”模式。
2. 用户上传 `.txt/.csv` 或粘贴多行问题。
3. 前端解析输入，生成 `BatchQuestion[]`。
4. 用户检查问题列表和 `topN/topK` 参数。
5. 用户点击“开始批量运行”。
6. 前端串行调用 `answerQuestionStream({query, top_n, top_k})`。
7. 每条问题根据 SSE 事件更新状态、步骤和增量回答。
8. 成功结果显示回答、引用、检索证据和 bad case 反馈。
9. 用户可对单条失败问题重试，或对单条成功结果提交 bad case。

## 错误处理

输入错误：

- 不支持的文件类型：提示“仅支持 txt 或 csv 文件”。
- 解析后无问题：提示“没有解析到可执行的问题”。
- 超过 100 条：提示“单批最多支持 100 个问题，请拆分后再运行”。

执行错误：

- 单条问题失败不终止整批，后续问题继续执行。
- 失败项展示 `answerQuestionStream` 抛出的安全错误摘要。
- 用户可以点击失败项的“重试”按钮，只重跑该条。
- 批量运行中禁用开始、清空和重新上传，避免状态交错。

后端错误：

- 继续沿用现有 `/api/answer/stream` 的安全错误处理。
- 不在前端展示 Python traceback、密钥、绝对路径或内部异常细节。

## 测试计划

前端单元测试：

- `.txt` 内容按非空行解析为问题列表。
- `.csv` 有 `question` 表头时读取 `question` 列。
- `.csv` 无 `question` 表头时读取第一列。
- 不支持的文件类型显示错误。
- 超过 100 条问题时禁止运行。
- 点击开始后按顺序调用 `/api/answer/stream`。
- 运行中展示每条问题的中间步骤和增量回答。
- 单条失败后继续执行下一条。
- 成功条目可提交 bad case，payload 包含该条 query、answer、citations 和 retrieval_evidences。
- 失败条目可单独重试。

后端测试：

- 首版无需新增批量后端接口测试。
- 现有 `/api/answer/stream`、`/api/bad-cases` 和安全错误测试保持通过。

人工验证：

- 用两行 `.txt` 文件上传后能顺序跑完。
- 用带 `question` 列的 `.csv` 上传后能顺序跑完。
- 运行中右侧索引状态不受影响。
- 点击批量结果引用后右侧溯源详情正确切换。
- 对批量结果提交 bad case 后 `.local/bad_cases/bad_cases.jsonl` 记录完整上下文。

## 实现范围

预计修改：

- `web/src/types.ts`：增加批量状态类型。
- `web/src/api.ts`：无需新增接口，继续复用 `answerQuestionStream` 和 `submitBadCase`。
- `web/src/App.tsx`：新增工作模式切换、批量解析、串行执行、单条重试和批量结果展示。
- `web/src/styles.css`：新增分段控件、批量输入区、批量结果列表和状态徽标样式。
- `web/src/App.test.tsx`：覆盖批量解析、执行状态、失败隔离、重试和 bad case 反馈。

不修改：

- RAG 核心模块。
- `src/xhbx_rag/web/app.py` 的问答接口契约。
- bad case 落盘结构。
