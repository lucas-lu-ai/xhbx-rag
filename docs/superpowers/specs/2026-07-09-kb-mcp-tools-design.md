# MCP 知识库 Tool 契约调整设计

## 背景

当前 `src/xhbx_rag/mcp_server.py` 默认暴露旧工具 `search_knowledge`、`retrieval_status` 和 `list_filter_options`。`docs/知识库 MCP Tool 文档.md` 要求面向下游 Agent 暴露知识库风格工具，并统一返回 `McpResponse<T>`：

- 成功：`{"success": true, "data": ...}`
- 失败：`{"success": false, "errorCode": "...", "errorMessage": "..."}`

本次目标是不删除旧检索能力，只调整默认暴露面：新增并暴露 `kb_list_knowledge_bases` 和 `kb_search_knowledge`，保留旧 `search_knowledge` 代码路径但默认不注册为 MCP tool，后续需要切回时可以重新启用。

## 方案

采用方案 A：单一 MCP server factory 默认注册新契约工具，并提供 legacy 暴露开关。

- `create_mcp_server(..., expose_legacy_tools=False)` 默认只注册 `kb_list_knowledge_bases` 和 `kb_search_knowledge`。
- 旧 `search_knowledge` 实现保留为内部函数或条件注册工具；当 `expose_legacy_tools=True` 时重新注册旧工具，用于回滚或兼容旧客户端。
- `retrieval_status` 和 `list_filter_options` 不再默认暴露。它们的底层 provider 与格式化逻辑可以继续保留，避免破坏已有测试辅助和后续排障能力。

## Tool 行为

### `kb_list_knowledge_bases`

无参数，返回当前固定可见知识库列表：

- `kbId=1`：保险绩优案例库
- `kbId=2`：培训课程库

返回格式为：

```json
{
  "success": true,
  "data": [
    {"kbId": 1, "name": "保险绩优案例库", "description": "..."},
    {"kbId": 2, "name": "培训课程库", "description": "..."}
  ],
  "errorCode": null,
  "errorMessage": null
}
```

### `kb_search_knowledge`

参数按文档接收：`query`、`kbId`、`knowledgeTypes`、`retrievalMode`、`hybridWeights`、`topK`。

本项目当前实际知识形态统一映射为 `SLICE`：

- `knowledgeTypes` 缺省时按文档接受 `QA / SLICE / KNOWLEDGE_POINT`，但实际只有 `SLICE` 会返回结果。
- 如果显式传入的 `knowledgeTypes` 不包含 `SLICE`，返回空 `data`，而不是报错。
- `retrievalMode` 默认 `HYBRID`。当前底层 MCP 检索链固定走向量 + 关键词混合召回与 rerank。为避免接口声明和实际行为不一致，`SPARSE` / `VECTOR` 暂时返回参数错误 `10004`。
- `hybridWeights` 暂时仅校验为对象或空值；底层仍使用现有 RRF 混合策略，不按权重调节。
- `topK` 默认 10，最大 50；传入小于 1 或大于 50 时返回 `10004`。

`kbId` 到过滤条件的映射：

- `kbId=1`：检索案例知识，过滤 `chunk_types=["customer_journey", "strategy", "script", "objection_handling"]`。
- `kbId=2`：检索课程知识，过滤 `chunk_types=["training_course"]`。

底层复用 `EvidenceSearcher.search(query, top_n, top_k, filters)`，其中 `top_n` 使用 `max(DEFAULT_TOP_N, topK)`，`top_k` 使用 `topK`。

## 返回映射

旧检索结果中的每条 `results[]` 映射为文档中的 item：

- `id`：旧 `chunk_id`
- `knowledgeType`：固定为 `SLICE`
- `score`：优先使用 `rerank_score`，没有则使用 `score`
- `tags`：来自 `metadata.tag_paths`，没有则为 `null`
- `qa`：固定为 `null`
- `knowledgePoint`：固定为 `null`
- `slice.content`：`text` 的预览文本，超长截断
- `slice.fullContent`：完整 `text`
- `slice.contentTruncated`：是否截断
- `slice.sliceType`：旧 `chunk_type`
- `slice.parentId`、`slice.titlePath`、`slice.parentSliceContext`：从 metadata 同名字段读取，没有则为 `null`
- `slice.citations`：保留旧 `citations` 扩展字段，便于客户端定位原文

## 错误处理

所有新工具都返回包装对象，不通过 MCP `ToolError` 暴露业务错误。

- `10004`：参数错误，例如 `query` 为空、`kbId` 为空、`topK` 超出范围、暂不支持的 `retrievalMode`
- `10003`：`kbId` 不在可见知识库列表中
- `500`：内部错误。错误消息复用 `_safe_error_message` 白名单逻辑，避免泄漏路径和堆栈

旧 legacy 工具保持现有抛错行为，避免影响旧客户端语义。

## 测试

按 TDD 修改 `tests/test_mcp_server.py`：

- 默认 `list_tools()` 只包含 `kb_list_knowledge_bases` 和 `kb_search_knowledge`。
- `expose_legacy_tools=True` 时可重新看到旧工具。
- `kb_list_knowledge_bases` 返回统一成功包装。
- `kb_search_knowledge` 会校验并传递 `query/topK/kbId` 映射后的 filters。
- 搜索结果会转换成文档要求的 `SLICE` 结构。
- 参数错误、无权限知识库、内部异常均返回统一失败包装，且内部路径不泄漏。

## 非目标

- 不改 Milvus schema、索引、检索核心或 rerank 策略。
- 不实现真正的 QA / KNOWLEDGE_POINT 独立知识形态。
- 不实现 `SPARSE` / `VECTOR` 独立召回模式。
- 不把 `hybridWeights` 接入底层排序。
