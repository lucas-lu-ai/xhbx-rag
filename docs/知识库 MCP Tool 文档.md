# 知识库 MCP Tool 文档

## 概述

本文档面向服务器问答智能体开发者。默认 `MCP_TOOL_PROFILE=kb` 只暴露
`kb_search_knowledge`，在统一 Milvus collection 中按一级领域检索知识切片。

所有响应统一包装为 `McpResponse<T>`：

- 成功：`{"success":true,"data":...,"errorCode":null,"errorMessage":null}`
- 失败：`{"success":false,"data":null,"errorCode":"...","errorMessage":"..."}`

标准 MCP 调用结果会把同一个业务对象放在 `CallToolResult.structuredContent` 和
`content[].text` 中。新调用方优先读取 `structuredContent`；只有旧客户端不支持时，
才解析第一条文本内容作为兼容回退。

## 一级领域体系与职责边界

`primaryDomains` 的非空元素只允许以下七个一级领域：

1. 产品知识
2. 合规与风控
3. 销售技能
4. 客户经营
5. 行业与公司
6. 个人成长
7. 组织发展

服务器问答智能体通过自身大模型理解用户问题并选择领域。MCP 不调用
chat/completions，不根据 query 自动分类，不调用本地关键词推断规则，也不按
`source_kind` 区分培训资料与绩优案例。

能够匹配现有一级体系时选择一个或多个最相关领域；跨领域问题可以多选。无法匹配
现有体系时传入空数组 `[]`，MCP 不生成领域过滤并查询全部文档，包括未标注或异常领域
的历史数据。

## `kb_search_knowledge`

### 用途

在调用方指定的一级领域范围内执行 embedding、Milvus 向量/关键词召回、RRF 融合和
rerank，返回知识切片。当前只实现 `SLICE`；`knowledgeTypes` 不包含 `SLICE` 时成功返回
空 `data`，不会生成 `QA` 或 `KNOWLEDGE_POINT`。

### 参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | string | 是 | 自然语言问题或关键词；去除首尾空白后不能为空 |
| `primaryDomains` | List\<string\> | 是 | 必传数组；允许 0 至 7 项。空数组表示全库检索，非空项为固定一级领域；去除首尾空白、自动去重并保留首次出现顺序 |
| `knowledgeTypes` | List\<string\> | 否 | 默认 `[QA, SLICE, KNOWLEDGE_POINT]`；当前仅实现 `SLICE` |
| `retrievalMode` | string | 否 | 默认且当前仅支持 `HYBRID` |
| `hybridWeights` | object | 否 | 兼容占位参数；若提供必须为对象，当前不参与排序 |
| `topK` | int | 否 | 返回数量，默认 10，范围 1 至 50 |
| `includeDetails` | boolean | 否 | 默认 `false`；控制精简或完整切片结构 |

`primaryDomains` 不是数组、含非字符串项、含体系外标签或超过七项时，返回包装后的
`10004`。空数组是合法的显式全库请求：

```json
{
  "success": false,
  "data": null,
  "errorCode": "10004",
  "errorMessage": "参数错误: primaryDomains 必须是由 0 到 7 个合法一级领域组成的数组"
}
```

合法参数会转换为内部过滤条件，例如：

```text
primary_domain in ["销售技能", "客户经营"]
```

指定领域没有召回结果时，MCP 成功返回空 `data`，不会自行放宽领域。服务器问答智能体可
决定是否改传空数组后重试；空数组会移除领域过滤并查询真正全库。

### 精简响应

`includeDetails` 省略或为 `false` 时，每条结果返回六个字段：

```json
{
  "success": true,
  "data": [
    {
      "docId": "pptx:案例A.pptx",
      "knowledgeType": "SLICE",
      "title": "切片",
      "content": "完整正文",
      "primaryDomain": "销售技能",
      "domainTags": ["销售技能", "客户经营"]
    }
  ],
  "errorCode": null,
  "errorMessage": null
}
```

`docId` 取第一条 citation 的 `source_id`，缺失时为空字符串；`primaryDomain` 缺失时为空
字符串；`domainTags` 缺失或格式非法时为空数组。

### 完整响应

`includeDetails=true` 时，`primaryDomain` 和 `domainTags` 位于结果顶层，`slice` 中保留
全文、预览、原业务切片类型和引用定位：

```json
{
  "success": true,
  "data": [
    {
      "id": "case_a_chunk_0001",
      "knowledgeType": "SLICE",
      "score": 0.98,
      "primaryDomain": "销售技能",
      "domainTags": ["销售技能", "客户经营"],
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

## 服务器问答智能体接入

建议加入系统提示词：

> 调用 kb_search_knowledge 时，primaryDomains 必须传入。能够匹配现有一级体系时，从产品知识、合规与风控、销售技能、客户经营、行业与公司、个人成长、组织发展中选择一个或多个最相关领域；无法匹配现有体系时传入空数组 []，由 MCP 查询全部文档。不得创造体系外分类，不得省略 primaryDomains。

调用示例：

```json
{
  "name": "kb_search_knowledge",
  "arguments": {
    "query": "客户担心保费太高怎么沟通",
    "primaryDomains": ["销售技能", "客户经营"],
    "knowledgeTypes": ["SLICE"],
    "retrievalMode": "HYBRID",
    "topK": 10,
    "includeDetails": true
  }
}
```

无法匹配现有一级体系时：

```json
{
  "name": "kb_search_knowledge",
  "arguments": {
    "query": "无法匹配现有体系的问题",
    "primaryDomains": []
  }
}
```

示例映射：

| 用户问题 | `primaryDomains` |
|---|---|
| 保险责任和现金价值怎么解释 | `["产品知识"]` |
| 客户说保费太贵怎么办 | `["销售技能", "客户经营"]` |
| 销售误导和双录要求 | `["合规与风控"]` |
| 新人增员和团队培养 | `["组织发展"]` |
| 无法匹配现有七类体系的问题 | `[]` |

服务器问答智能体升级后需要刷新 MCP 工具 schema 缓存，并通过 `tools/list` 确认 required
参数为 `query` 和 `primaryDomains`。

## 错误码

| `errorCode` | 含义 |
|---|---|
| `10004` | 参数错误，如 query 为空、一级领域非法、topK 越界、retrievalMode 非 HYBRID 或 hybridWeights 非对象 |
| `500` | 系统内部错误；对外消息经过安全化，不泄漏内部路径或堆栈 |

显式启用 `legacy` 或 `both` profile 时，旧调试工具仍可使用；新服务器问答智能体必须调用
上述一级领域契约。

> 注：内容由 AI 生成，请谨慎参考。
