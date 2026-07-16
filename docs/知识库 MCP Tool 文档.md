# 知识库 MCP Tool 文档

# 概述

本文档面向下游 Agent 开发者，说明知识库 MCP Server 暴露的 Tool 接口、参数及返回结构。

所有 Tool 返回统一包装为 `McpResponse<T>`：

- 成功：`{"success":true,"data":...}`
- 失败：`{"success":false,"errorCode":"...","errorMessage":"..."}`

# Tool 列表

## kb\_list\_knowledge\_bases

**用途：**列出当前用户有权限查阅的知识库。

**调用时机：**当用户询问有哪些知识库可用，或需要先确认可检索范围时调用。

### 参数

无参数。

### 返回 data

```json
[{"kbId": 1,"name": "产品知识库","description": "..."}]
```

## kb\_search\_knowledge

**用途：**在指定知识库中检索文档切片。

**调用时机：**当用户提出具体问题、需要召回知识内容片段时调用。调用前应先使用 `kb_list_knowledge_bases` 获取可见知识库 ID。

当前服务仅实现并返回 `SLICE`。`knowledgeTypes` 不包含 `SLICE` 时，成功响应的 `data` 为空数组；`QA` 和 `KNOWLEDGE_POINT` 暂不产生对应结果。

### 参数

|字段|类型|必填|说明|
|---|---|---|---|
|query|string|是|检索 query，支持自然语言问题或关键词|
|kbId|long|是|目标知识库 ID，必须来自 kb\_list\_knowledge\_bases 返回的可见知识库|
|knowledgeTypes|List\<string\>|否|知识形态过滤，默认 `[QA, SLICE, KNOWLEDGE_POINT]`；当前仅实现 `SLICE`，列表不含 `SLICE` 时返回空 `data`|
|retrievalMode|string|否|检索方式，默认且当前仅支持 `HYBRID`；传入 `SPARSE`、`VECTOR` 或其他值返回参数错误 `10004`|
|hybridWeights|object|否|兼容占位参数；若提供则必须为对象，否则返回 `10004`。当前不读取其中权重，也不影响检索排序|
|topK|int|否|返回数量，默认 10，最大 50|
|includeDetails|boolean|否|是否返回完整切片结构，默认 false。省略或设为 false 时返回精简结构；设为 true 时返回原完整结构|

标准 MCP 调用结果将业务对象放在 `CallToolResult.structuredContent`；
新调用方应直接读取该字段，无需解析 `content[].text`。
`content[].text` 仅作为旧客户端兼容回退保留。

### 返回 data

`includeDetails` 省略或为 `false` 时，每条结果返回四字段精简切片摘要：`docId` 取第一条 citation 的 `source_id`，缺失时为空字符串；`knowledgeType` 固定为 `SLICE`，`title` 固定为 `切片`，`content` 为完整正文（缺失时为空字符串）：

```json
{
  "success": true,
  "data": [
    {
      "docId": "pptx:案例A.pptx",
      "knowledgeType": "SLICE",
      "title": "切片",
      "content": "完整正文"
    }
  ],
  "errorCode": null,
  "errorMessage": null
}
```

`includeDetails` 为 `true` 时返回原完整切片结构。当前结果的 `knowledgeType` 固定为 `SLICE`。`slice.citations` 是平台契约外的扩展字段，承载原文来源与定位信息，例如来源路径、文件名、页码或锚点：

```json
{
  "success": true,
  "data": [
    {
      "id": "case_a_chunk_0001",
      "knowledgeType": "SLICE",
      "score": 0.98,
      "tags": ["异议处理/预算"],
      "qa": null,
      "slice": {
        "content": "先共情客户对预算的顾虑，再澄清其保障需求。",
        "fullContent": "先共情客户对预算的顾虑，再澄清其保障需求。",
        "contentTruncated": false,
        "sliceType": "script",
        "parentId": "case_a",
        "titlePath": ["案例A", "预算异议"],
        "parentSliceContext": null,
        "citations": [
          {
            "source_path": "案例A/预算异议.txt",
            "filename": "预算异议.txt",
            "source_type": "txt",
            "locator": {"line_start": 12, "line_end": 16},
            "locator_confidence": "exact",
            "anchor_id": "budget-objection"
          }
        ]
      },
      "knowledgePoint": null
    }
  ],
  "errorCode": null,
  "errorMessage": null
}
```

# 调用示例

## 1\. 获取知识库列表

```json
{"name": "kb_list_knowledge_bases","arguments": {}}
```

## 2\. 默认精简检索

```json
{"name": "kb_search_knowledge","arguments": {"query": "预算异议怎么处理","kbId": 1,"knowledgeTypes": ["SLICE"],"topK": 5}}
```

## 3\. 获取完整切片结构

```json
{"name": "kb_search_knowledge","arguments": {"query": "预算异议怎么处理","kbId": 1,"knowledgeTypes": ["SLICE"],"topK": 5,"includeDetails": true}}
```

# 错误码

|errorCode|含义|
|---|---|
|10004|参数错误，如 query 为空、topK 超出范围、retrievalMode 不是 HYBRID，或 hybridWeights 不是对象|
|10003|当前用户对指定知识库无访问权限|
|500|系统内部错误|

> (注：内容由 AI 生成，请谨慎参考）
