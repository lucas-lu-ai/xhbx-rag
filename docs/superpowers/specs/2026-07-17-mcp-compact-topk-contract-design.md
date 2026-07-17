# MCP 检索精简返回与 topK 契约设计

## 背景

`kb_search_knowledge` 的默认精简模式原本只返回 `docId`、`knowledgeType`、
`title`、`content` 四个字段。统一知识库元数据改动后来向精简结果加入了
`primaryDomain` 和 `domainTags`，导致调用方收到的数据结构偏离既定契约。

此外，`topK` 虽然已经传给底层检索器，但 MCP 格式化层没有独立限制输出数量。
接口层需要保证即使底层意外返回更多候选，最终 `data` 也不会超过 `topK`。

## 目标契约

当 `includeDetails` 省略或为 `false` 时，成功结果通过
`CallToolResult.structuredContent` 返回以下业务对象：

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

每个 `data` 元素必须且只能包含上述四个字段：

- `docId`：第一条 citation 的 `source_id`，缺失时为空字符串。
- `knowledgeType`：固定为 `SLICE`。
- `title`：固定为 `切片`。
- `content`：检索结果的完整正文，缺失时为空字符串。

`data` 按检索器返回顺序保留，数量最多为请求参数 `topK`。结果不足时按实际数量
返回，不补空对象。

## 实现方案

采用接口层双重约束：

1. `topK` 继续传给底层检索器，维持召回、排序和性能行为。
2. 精简格式化函数接收已校验的 `topK`，只遍历前 `topK` 条原始结果。
3. 从精简结果中删除 `primaryDomain` 和 `domainTags`。

此方案不改变检索排序，也不修改底层搜索结果结构。

## 保持不变的行为

- `includeDetails=true` 继续返回现有完整切片结构，包括领域元数据。
- 顶层 `success/data/errorCode/errorMessage` 包装不变。
- `structuredContent` 与 `content[].text` 兼容回退不变。
- `topK` 默认值 10、合法范围 1 至 50 不变。
- 参数错误、权限错误和系统错误的现有错误码不变。
- `primaryDomains`、`knowledgeTypes`、`retrievalMode` 和 `hybridWeights` 行为不变。

## 测试策略

采用测试驱动修复：

1. 先把默认精简模式测试改为严格比较四字段，确认当前实现因多余字段失败。
2. 新增底层返回数量超过 `topK` 的测试，确认当前格式化层会超量返回。
3. 最小修改生产代码后，验证精简模式字段和数量契约通过。
4. 运行 MCP 全部测试，确认完整模式、协议层 `structuredContent` 和错误路径不回归。

## 非目标

- 不删除 `includeDetails` 参数。
- 不修改完整模式结构。
- 不调整向量召回、关键词召回、融合或 rerank 算法。
- 不改变统一知识库的领域元数据存储和检索过滤。
