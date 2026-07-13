# MCP 精简检索返回设计

## 目标

为新版 `kb_search_knowledge` 增加按请求切换返回结构的能力。默认保持现有完整结构，避免破坏已有调用方；调用方明确关闭详情后，每条检索结果只返回正文和第一条引用来源。

## 接口设计

`kb_search_knowledge` 新增布尔参数：

```python
includeDetails: bool = True
```

- `includeDetails=true`：沿用当前完整的 `McpResponse` 和 `data` 条目结构。
- `includeDetails=false`：仍沿用外层 `McpResponse`，但 `data` 中每条结果只包含 `content`、`source_path`、`filename`。
- 旧版 `search_knowledge` 不变。

精简模式的成功响应示例：

```json
{
  "success": true,
  "data": [
    {
      "content": "检索到的完整正文",
      "source_path": "案例A/a.txt",
      "filename": "a.txt"
    }
  ],
  "errorCode": null,
  "errorMessage": null
}
```

## 字段映射

- `content` 取检索结果的完整 `text`，不使用截断预览。
- `source_path` 取第一条 citation 的 `source_path`。
- `filename` 取第一条 citation 的 `filename`。
- 一条结果存在多条 citation 时，只读取第一条。
- citation 缺失、为空、第一条不是对象或相应字段缺失时，`source_path`、`filename` 返回空字符串。
- 正文缺失时，`content` 返回空字符串。

## 数据流与错误处理

检索、过滤、融合和重排流程保持不变。检索成功后，根据 `includeDetails` 选择完整格式化或精简格式化，再用现有 `_mcp_success` 包装。参数校验、权限错误和内部错误继续使用现有错误响应，不受精简模式影响。

## 测试范围

在 MCP 服务测试中覆盖：

1. 不传 `includeDetails` 时仍返回原完整结构。
2. 显式传 `includeDetails=true` 时返回原完整结构。
3. 传 `includeDetails=false` 时仅返回三个指定字段。
4. 多条 citation 时只使用第一条。
5. 无 citation 或 citation 字段缺失时返回空字符串。
6. 工具输入 schema 暴露 `includeDetails`，默认值为 `true`。

