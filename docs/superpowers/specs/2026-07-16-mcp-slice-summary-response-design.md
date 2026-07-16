# MCP 切片摘要返回设计

## 目标

调整新版 `kb_search_knowledge` 的精简返回结构。默认或显式设置 `includeDetails=false` 时，每条结果只返回文档标识、固定知识类型、固定标题和完整正文；显式设置 `includeDetails=true` 时继续返回现有完整切片结构。

## 接口行为

工具参数保持：

```python
includeDetails: bool = False
```

- 省略 `includeDetails` 或传入 `false`：返回新的四字段精简结构。
- 传入 `true`：返回现有完整切片结构，不改变字段或语义。
- 外层继续使用现有 `McpResponse`，错误响应保持不变。

精简模式成功响应示例：

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

## 字段映射

- `docId`：第一条 citation 的 `source_id`。
- `knowledgeType`：固定为 `SLICE`。
- `title`：固定为 `切片`。
- `content`：检索结果的完整 `text`，不截断。
- 一条结果存在多条 citation 时，只读取第一条。
- citation 缺失、为空、第一条不是对象、`source_id` 缺失或为空时，`docId` 返回空字符串。
- `text` 缺失或为空时，`content` 返回空字符串。
- 精简条目不得返回 `source_path`、`filename` 或其他额外字段。

## 数据流与兼容性

检索、知识库过滤、混合召回、重排和错误处理均保持不变。只替换精简结果格式化函数生成的条目字段；完整模式继续调用现有 `_format_kb_search_results`。旧版 `search_knowledge` 不受影响。

## 测试范围

MCP 服务测试覆盖：

1. 工具 schema 中 `includeDetails` 默认值仍为 `false`。
2. 省略 `includeDetails` 时严格返回四个指定字段。
3. 显式设置 `includeDetails=false` 时严格返回四个指定字段。
4. 显式设置 `includeDetails=true` 时仍返回原完整结构。
5. 多条 citation 时 `docId` 只取第一条的 `source_id`。
6. citation 或 `source_id` 缺失及为空时 `docId` 为空字符串。
7. 正文缺失时 `content` 为空字符串。
8. README、正式 MCP 契约及私有化部署文档与新默认结构一致。

