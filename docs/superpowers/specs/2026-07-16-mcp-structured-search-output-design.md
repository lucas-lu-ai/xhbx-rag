# MCP 检索结构化输出设计

## 目标

让标准 MCP 调用方无需再次解析 `content[].text` 中的 JSON 字符串，即可从 `CallToolResult.structuredContent` 直接取得 `kb_search_knowledge` 的业务响应对象。

结构化业务对象保持：

```json
{
  "success": true,
  "data": [
    {
      "docId": "pptx:示例文件.pptx",
      "knowledgeType": "SLICE",
      "title": "切片",
      "content": "检索到的完整正文"
    }
  ],
  "errorCode": null,
  "errorMessage": null
}
```

## 根因

当前 `kb_search_knowledge` 使用裸返回注解 `-> dict`。FastMCP 1.28.1 无法从裸 `dict` 生成工具输出 Schema，因此将返回字典作为非结构化内容转换成 `TextContent.text`，调用方只能取得字符串并进行二次 JSON 解析。

FastMCP 对 `dict[str, Any]` 可以自动生成对象类型的 `outputSchema`。工具执行时会同时生成结构化对象与旧版文本内容；标准 MCP 协议层将对象写入 `CallToolResult.structuredContent`。

## 接口设计

只修改新版检索工具的返回注解：

```python
def kb_search_knowledge(...) -> dict[str, Any]:
```

- `kb_search_knowledge` 的业务函数仍返回现有 Python 字典，不改变成功或失败对象字段。
- 工具列表中的 `kb_search_knowledge.outputSchema` 为对象 Schema。
- 调用方直接读取 `result.structuredContent`，得到完整业务对象，不需要 `JSON.parse`。
- 标准 MCP 的 `CallToolResult` 外壳仍然存在，这是协议要求。
- FastMCP 生成的 `content[].text` 继续作为旧客户端兼容回退；新调用方不使用它。
- `kb_list_knowledge_bases`、旧版 `search_knowledge`、`retrieval_status` 和 `list_filter_options` 不在本次范围。

## 业务兼容性

- 省略 `includeDetails` 或传入 `false` 时，`structuredContent.data` 每条结果仍严格包含 `docId`、`knowledgeType`、`title`、`content`。
- 显式传入 `includeDetails=true` 时，`structuredContent.data` 仍为现有完整切片结构。
- 参数错误、权限错误和内部错误同样通过 `structuredContent` 返回现有 `McpResponse` 错误对象。
- 检索、过滤、混合召回、重排和错误归一逻辑不变。

## 测试范围

通过真实 FastMCP 工具注册和调用路径覆盖：

1. `kb_search_knowledge` 声明对象类型的 `outputSchema`。
2. 默认精简成功响应可直接从结构化结果读取，无需解析文本。
3. `includeDetails=true` 的完整响应可直接从结构化结果读取。
4. query 为空、无权限知识库及检索内部错误的业务错误对象可直接从结构化结果读取。
5. 兼容文本内容仍存在，并与结构化对象表达相同业务数据。
6. 其他 MCP 工具的现有返回行为不改变。
7. README、正式 MCP 契约和私有化部署文档明确新调用方读取 `structuredContent`。

