# RAG Web 问答界面设计

日期：2026-07-01

## 背景

当前 `xhbx-rag` 已经具备完整的命令行 RAG 问答链路：query understanding、embedding、Milvus Lite 检索、BM25 混合召回、rerank、基于证据的回答生成和 citation 输出。现有 `--studio` 能把步骤 trace 发到 AgentScope Studio，但不能在 Studio 页面中直接作为聊天界面使用。

本设计新增一个本机 Web 交互层，让用户可以在浏览器里直接提问、查看回答、点击引用查看溯源信息。第一版只新增 Web 层，不修改 RAG 核心链路。

## 范围

第一版包含：

- 本机单人使用，启动后访问 `localhost`。
- FastAPI 后端和 React/Vite 前端。
- Web 问答接口调用现有 `answer_query()`。
- 页面显示索引状态、问答历史、回答、引用列表和引用详情。
- 点击引用后在右侧显示文件路径、来源类型、locator、原文摘录和定位置信心。
- 原始数据根目录固定为项目内 `data/`。
- 支持在 Finder 中显示引用对应的本地文件。

第一版不包含：

- Web 内一键执行 `generate-insights -> parse -> index`。
- 多用户、登录、权限控制。
- 完整多轮上下文记忆。
- 在 Word/PPT/PDF 内精确跳转到段落或页内锚点。
- 修改现有 CLI 和 RAG 核心模块的行为。

## 推荐方案

采用 FastAPI + React/Vite，复用现有 RAG 函数。

这个方案的取舍：

- 优点：复用现有核心链路，风险低；CLI 仍可独立使用；Web 只承担交互和结果呈现。
- 缺点：第一版不做入库任务编排，也不做文档内精确预览跳转。

未采用的方案：

- 完整 Web 控制台：包含入库、生成洞察、parse、index 和任务日志，范围过大，容易把长耗时模型任务和失败恢复引入第一版。
- Gradio/Streamlit：能快速出界面，但引用选择、右侧详情、Finder 操作和后续产品化会受限。

## 架构

新增 `web` 层，保留现有 RAG 核心链路。

后端职责：

- 读取 `RetrievalConfig.from_env()`。
- 构造现有 `QueryUnderstandingAgent`、`EmbeddingClient`、`MilvusLiteStore`、`RerankClient`、`AnswerAgent`。
- 暴露本机 API 给前端调用。
- 对引用路径做 `data/` 根目录安全校验。
- 在 macOS 上提供 Finder reveal 操作。

前端职责：

- 提供问答输入、加载态、错误态和问答历史。
- 展示回答和引用列表。
- 在右侧展示选中引用的溯源详情。
- 展示索引和配置状态。
- 在窄屏下从双栏布局降级为单列布局。

RAG 核心模块职责不变：

- `answer_query()` 负责检索和回答生成。
- `search_evidence()` 负责 query 改写、召回、融合和 rerank。
- `MilvusLiteStore` 负责本地 Milvus Lite 访问。

## API 设计

### `GET /api/status`

返回本机运行状态。

响应字段：

- `ok`: 后端是否可以基本运行。
- `data_dir`: 数据目录，固定为 `data`。
- `milvus_lite_path`: Milvus Lite 数据库路径。
- `milvus_collection`: collection 名称。
- `config`: 必要配置项是否存在，不返回密钥值。
- `errors`: 可修复错误列表。

第一版不统计全量文档数量，避免引入额外扫描和索引查询复杂度。

### `POST /api/answer`

请求：

```json
{
  "query": "客户说每年不能超过80万怎么办？",
  "top_n": 20,
  "top_k": 5
}
```

响应复用现有 `answer_query()` 结果，并补充 UI 字段：

```json
{
  "original_query": "客户说每年不能超过80万怎么办？",
  "rewritten_query": "客户每年交费不能超过80万时如何处理预算异议？",
  "intent": "objection_handling",
  "filters": {},
  "answer": "可以先承接预算边界，再转向缴费期和保障缺口。",
  "citations": [
    {
      "filename": "第1节.track-0.txt",
      "source_type": "txt",
      "source_path": "data/案例A/第1节/第1节.track-0.txt",
      "locator": {"line_start": 2, "line_end": 2},
      "locator_confidence": "validated_span",
      "source_excerpt": "客户说每年保费预算不能超过80万",
      "display_location": "L2",
      "display_excerpt": "客户说每年保费预算不能超过80万",
      "can_reveal": true
    }
  ],
  "evidence_count": 1
}
```

`display_location` 由 locator 计算：

- `page` 显示为 `pN`。
- `slide` 显示为 `slideN`。
- `line_start/line_end` 显示为 `LN` 或 `LN-LM`。
- `heading_path` 作为辅助位置文本。
- 无 locator 时显示 `未提供精确位置`。

### `POST /api/source/reveal`

请求：

```json
{
  "source_path": "data/案例A/第1节/第1节.track-0.txt"
}
```

行为：

- 解析路径并确认它位于项目 `data/` 目录内。
- 如果路径包含 `::`，只取真实文件部分，例如 `data/a.docx::word/media/image1.png` 映射到 `data/a.docx`。
- 文件存在时，在 macOS Finder 中显示文件。
- 文件不存在、路径越界或不是普通文件时返回明确错误。

## 数据流

1. 用户在 React 输入框提交问题。
2. 前端校验非空，进入 loading 状态并禁用发送按钮。
3. 前端调用 `POST /api/answer`。
4. 后端调用现有 `answer_query()`。
5. 后端把 citation 转换为前端友好的溯源字段。
6. 前端把本轮问答追加到页面状态。
7. 用户点击引用，前端仅更新右侧详情面板，不重新请求模型。
8. 用户点击“在 Finder 中显示文件”，前端调用 `POST /api/source/reveal`。

第一版保留问答历史用于页面展示，但每次后端问答都是单轮独立请求，不把历史注入检索 query 或回答模型。

## UI 设计

界面定位为“本机知识库问答工作台”，而不是营销页。

布局：

- 桌面端使用左右双栏。
- 左侧为问答主流程：标题、状态提示、问答历史、回答、引用按钮、输入区。
- 右侧为辅助面板：索引状态和选中引用详情。
- 窄屏下切换为单列：问答在上，溯源详情在下。

视觉基准：

- 背景使用中性浅灰。
- 工作区使用白色 surface。
- 蓝色只用于主要操作、当前引用和可交互强调。
- 错误、成功、禁用等状态使用语义色 token。
- 不使用装饰性渐变、浮动大卡片或营销式 hero。

交互规则：

- 输入框有可见 label，不只依赖 placeholder。
- 发送中禁用按钮并显示加载状态。
- 引用按钮有选中态，选中后右侧详情同步更新。
- 无引用时右侧显示空状态。
- 所有按钮和引用卡片支持键盘 focus。
- 点击区域不小于 44px。
- 加载超过 300ms 时显示明确反馈。
- 动画仅用于状态切换，时长控制在 150-300ms，并尊重 `prefers-reduced-motion`。

推荐设计 token：

- `--color-background`: `#F8FAFC`
- `--color-surface`: `#FFFFFF`
- `--color-foreground`: `#1E293B`
- `--color-muted`: `#64748B`
- `--color-border`: `#E2E8F0`
- `--color-accent`: `#2563EB`
- `--color-success`: `#166534`
- `--color-error`: `#DC2626`

字体：

- 第一版优先使用系统字体栈，避免本机工具因外部字体加载产生闪烁或网络依赖。
- 字号从 12、14、16、18、24 形成紧凑层级。
- 长回答正文行高保持在 1.5-1.7。

## 错误处理

配置错误：

- `/api/status` 返回缺失项。
- 页面顶部状态条提示需要修复的环境变量或 Milvus 配置。
- 问答请求也返回可读错误，不展示 Python traceback。

检索无结果：

- 不是系统错误。
- 回答区展示“当前检索结果不足以确认。”
- 引用区显示空状态。

模型或网络失败：

- 回答卡片展示失败摘要。
- 提供重试按钮。
- 后端日志保留详细异常，API 响应只返回安全摘要。

溯源失败：

- 引用详情仍展示已有 citation 信息。
- `can_reveal=false` 时禁用 Finder 按钮。
- 路径越界、文件不存在、路径解析失败分别给出明确提示。

## 安全边界

本项目第一版仅面向本机 `localhost` 使用，但仍保留基础安全约束：

- `/api/source/reveal` 只允许访问项目 `data/` 目录内文件。
- 不返回任何 API key 或密钥值。
- 不提供任意文件读取 API。
- 不允许通过 `../`、绝对路径或 symlink 逃逸 `data/` 根目录。
- 前端只展示后端返回的安全路径字段。

## 测试计划

后端测试：

- `/api/status` 在配置完整和配置缺失时返回预期字段。
- `/api/answer` 能调用封装层并返回标准问答结构。
- citation UI 字段能正确处理 txt 行号、pdf 页码、pptx slide、docx 标题路径和缺失 locator。
- `/api/source/reveal` 拒绝 `../`、越界绝对路径、缺失文件和 `data/` 外路径。
- `::` 形式的嵌入资源路径能映射到真实宿主文件。

前端测试或人工验证：

- 空问题不能提交。
- loading 时发送按钮禁用。
- 成功回答后，引用列表可选中并更新右侧详情。
- 无引用时右侧显示空状态。
- 后端错误时显示可恢复提示和重试入口。
- 375px、768px、1024px、1440px 宽度下无横向溢出。
- 键盘 Tab 顺序符合视觉顺序。

回归测试：

- 保留并运行现有 RAG 单元测试，确认核心链路行为不因 Web 层新增而改变。
- 新增 Web 层测试独立 mock RAG 调用，避免测试依赖真实模型服务。

## 实施顺序

1. 新增后端 Web API 模块和测试。
2. 新增 React/Vite 前端骨架和设计 token。
3. 实现问答请求、loading、错误态和问答历史。
4. 实现引用列表和右侧溯源详情。
5. 实现 Finder reveal API 和前端按钮。
6. 增加开发启动说明。
7. 运行后端测试、前端构建和浏览器视觉验证。

## 验收标准

- 用户可以在 `localhost` 页面输入问题并得到回答。
- 回答结果包含引用时，用户可以点击引用查看原文摘录和位置。
- 引用路径只允许指向 `data/` 下文件。
- 可以在 Finder 中显示存在的来源文件。
- 没有索引、配置缺失、模型失败、无结果时都有明确状态反馈。
- 不修改现有 RAG 核心链路和 CLI 行为。
- 桌面和移动窄屏布局都可用，没有文本重叠或横向滚动。
