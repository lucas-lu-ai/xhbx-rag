# MCP 一级领域检索契约改造设计

日期：2026-07-16

## 1. 背景

当前统一索引 `xhbx_knowledge_chunks` 已为每个 chunk 写入以下元数据：

- `primary_domain`：唯一主领域；
- `domain_tags`：一个或多个一级领域标签；
- `source_kind`：培训资料或绩优案例。

一级领域固定为：

1. 产品知识
2. 合规与风控
3. 销售技能
4. 客户经营
5. 行业与公司
6. 个人成长
7. 组织发展

现有 MCP 工具 `kb_search_knowledge` 仍要求调用方先通过 `kbId` 选择“绩优案例库”或“培训课程库”，服务端再把 `kbId` 转换为 `chunk_type` 过滤。这与“一个物理 collection、只按七类一级体系区分检索”的目标不一致。

服务器问答智能体能够调用大模型理解用户问题，MCP 服务不能也不应承担大模型分类职责。因此，一级领域判断由服务器问答智能体完成；MCP 只校验调用参数、执行确定性的 Milvus 过滤和检索。

## 2. 目标

- 从 `kb_search_knowledge` 删除 `kbId`。
- 不向 MCP 调用方暴露 `source_kind`，也不按培训资料/绩优案例过滤。
- 由服务器问答智能体必传 `primaryDomains`，明确指定要检索的一级领域。
- MCP 按 `primary_domain` 硬过滤，然后执行现有混合召回和 rerank。
- 检索结果返回 `primaryDomain` 和 `domainTags`，供问答智能体核验来源领域。
- 保持现有 `McpResponse` 业务包装和 `structuredContent` 读取方式。

## 3. 非目标

- 不在 MCP 内调用 chat/completions 或其他大模型。
- 不在 MCP 内根据 query 自动判断一级领域。
- 不使用本地关键词规则 `infer_query_domains` 代替服务器问答智能体。
- 不重新切片、不重新生成 embedding、不重新入库。
- 不修改 Milvus collection schema。
- 不在本次改造中删除显式启用的 `legacy` 工具 profile；服务器问答智能体必须使用默认 `kb` profile 的新契约。

## 4. 对外工具契约

### 4.1 工具列表

默认 `MCP_TOOL_PROFILE=kb` 只暴露：

- `kb_search_knowledge`

删除 `kb_list_knowledge_bases`。删除以下只服务于 `kbId` 的常量和映射：

- `KB_CASE_ID`
- `KB_COURSE_ID`
- `VISIBLE_KNOWLEDGE_BASES`
- `CASE_KB_CHUNK_TYPES`
- `COURSE_KB_CHUNK_TYPES`
- `_kb_filters`

`MCP_TOOL_PROFILE=legacy` 和 `MCP_TOOL_PROFILE=both` 暂时保留既有旧工具，用于存量调试或灰度；服务器问答智能体不得调用这些旧工具。`both` profile 中的新工具仍使用本设计定义的参数。

### 4.2 `kb_search_knowledge` 参数

新签名：

```python
kb_search_knowledge(
    query: str,
    primaryDomains: list[str],
    knowledgeTypes: list[str] | None = None,
    retrievalMode: str = "HYBRID",
    hybridWeights: dict[str, Any] | None = None,
    topK: int = 10,
    includeDetails: bool = False,
) -> dict[str, Any]
```

`query` 和 `primaryDomains` 是必填参数。`primaryDomains` 的工具描述必须列出七个合法值，使服务器问答智能体可以通过 `tools/list` 获取分类约束。

调用示例：

```json
{
  "query": "客户担心保费太高怎么沟通",
  "primaryDomains": ["销售技能", "客户经营"],
  "knowledgeTypes": ["SLICE"],
  "retrievalMode": "HYBRID",
  "topK": 10,
  "includeDetails": true
}
```

保留 `knowledgeTypes`、`retrievalMode`、`hybridWeights`、`topK` 和 `includeDetails` 的现有行为，以缩小服务器问答智能体的迁移范围。

### 4.3 `primaryDomains` 校验

MCP 在业务函数内执行校验，以便继续返回统一的 `McpResponse`，而不是把非法领域暴露为无包装的协议异常。

校验规则：

- 必须是非空数组；
- 每项必须是字符串；
- 去除项目前后空白；
- 只能包含七个固定一级领域；
- 自动去重，并按调用方首次出现顺序保留；
- 最少 1 类，最多 7 类。

空数组、非数组、非字符串项或未知分类返回：

```json
{
  "success": false,
  "data": null,
  "errorCode": "10004",
  "errorMessage": "参数错误: primaryDomains 必须包含 1 到 7 个合法一级领域"
}
```

原有由未知 `kbId` 触发的 `10003` 场景从新工具中删除；其他参数错误仍使用 `10004`，内部检索失败仍使用安全化后的 `500`。

## 5. 检索流程

MCP 收到合法调用后执行以下固定流程：

1. 将 `primaryDomains` 转换为内部过滤参数 `primary_domains`；
2. Milvus 构造过滤表达式：

   ```text
   primary_domain in ["销售技能", "客户经营"]
   ```

3. 在统一 collection 中执行向量召回；
4. 在同一过滤范围内执行关键词召回；
5. 使用 RRF 融合候选；
6. 使用现有 rerank 服务排序并截取 `topK`；
7. 返回统一 `McpResponse`。

MCP 不调用 `infer_query_domains`，不执行自动软加权，也不在无结果时擅自扩大检索领域。若指定领域无结果，成功返回空 `data`，由服务器问答智能体决定是否扩大 `primaryDomains` 后重试。

传入全部七类时，过滤条件覆盖当前完整一级体系，等价于在领域维度检索全库。服务器问答智能体对无法可靠分类的综合问题应使用这一方式，而不是省略必填参数。

## 6. 服务器问答智能体职责

服务器问答智能体在调用 MCP 前，通过自身大模型从七类体系中选择 1 至 3 个最相关领域；跨领域问题允许多选，无法可靠判断时传全部七类。

建议加入服务器问答智能体的工具调用提示：

> 调用 kb_search_knowledge 前，必须根据用户问题从产品知识、合规与风控、销售技能、客户经营、行业与公司、个人成长、组织发展中选择 1 至 3 个最相关领域，并通过 primaryDomains 传入。跨领域问题可以多选；无法可靠判断时传入全部七类。不得创造体系外分类，不得省略 primaryDomains。

示例映射：

| 用户问题 | `primaryDomains` |
|---|---|
| 保险责任和现金价值怎么解释 | `["产品知识"]` |
| 客户说保费太贵怎么办 | `["销售技能", "客户经营"]` |
| 销售误导和双录要求 | `["合规与风控"]` |
| 新人增员和团队培养 | `["组织发展"]` |

调用方继续优先读取 `CallToolResult.structuredContent`；只有旧 MCP Client 不支持该字段时，才解析 `content[].text` 作为兼容回退。

## 7. 返回结构

### 7.1 精简模式

`includeDetails=false` 时，在原精简字段基础上增加一级领域信息：

```json
{
  "docId": "pptx:案例A.pptx",
  "knowledgeType": "SLICE",
  "title": "切片",
  "content": "完整正文",
  "primaryDomain": "销售技能",
  "domainTags": ["销售技能", "客户经营"]
}
```

### 7.2 完整模式

`includeDetails=true` 时，在结果顶层增加：

```json
{
  "id": "chunk-1",
  "knowledgeType": "SLICE",
  "score": 0.98,
  "primaryDomain": "销售技能",
  "domainTags": ["销售技能", "客户经营"],
  "slice": {
    "fullContent": "完整正文",
    "citations": []
  }
}
```

`primaryDomain` 缺失时返回空字符串，`domainTags` 缺失或格式非法时返回空数组，避免单条历史脏数据破坏整个工具响应。

## 8. 兼容性与部署顺序

本次改造是有意的不兼容契约升级：

- 旧调用中的 `kbId` 不再合法；
- `primaryDomains` 从不存在变为必填；
- `kb_list_knowledge_bases` 不再注册；
- 精简结果增加两个字段。

MCP 与服务器问答智能体必须协调切换：

1. 在测试环境部署新 MCP；
2. 使用 `tools/list` 确认 `kb_search_knowledge` 的 required 参数为 `query` 和 `primaryDomains`；
3. 更新服务器问答智能体的工具参数模型与提示词；
4. 用七类代表问题完成联调；
5. 同一维护窗口升级生产 MCP 和服务器问答智能体；
6. 刷新服务器问答智能体的 MCP 工具 schema 缓存；
7. 观察 MCP 安全错误和空结果率。

不保留 `kbId` 兼容分支。若服务器 Agent 仍发送 `kbId`，应视为调用方未完成升级并在联调阶段修复。

## 9. 文档与脚本更新

实现时同步更新：

- `README.md` 的 MCP 工具说明；
- `docs/知识库 MCP Tool 文档.md`；
- `scripts/test_mcp.sh`，以环境变量或命令参数接收 `primaryDomains`；
- 默认工具 profile 的说明；
- 服务器问答智能体接入示例。

## 10. 测试与验收

必须覆盖：

1. 默认 `kb` profile 只注册 `kb_search_knowledge`；
2. `tools/list` 显示 `query`、`primaryDomains` 为必填；
3. 合法单领域和多领域参数转换为 `primary_domains` 过滤；
4. 重复领域按首次出现顺序去重；
5. 空数组、未知领域、非字符串项返回包装后的 `10004`；
6. 新工具不再接受或处理 `kbId`；
7. 传全部七类能够检索统一 collection；
8. 精简与完整结果都返回 `primaryDomain`、`domainTags`；
9. `structuredContent` 与文本回退承载相同业务对象；
10. `knowledgeTypes` 不含 `SLICE` 时仍返回成功空数组；
11. 非 `HYBRID`、非法 `topK` 和内部异常继续遵守现有错误契约；
12. MCP 检索链路没有 chat/completions 调用，也没有 `infer_query_domains` 调用；
13. 默认 MCP 测试脚本能以 `primaryDomains` 完成一次真实检索。

验收标准：服务器问答智能体只通过 query 和必填的七类 `primaryDomains` 调用 MCP；MCP 在统一 collection 中按 `primary_domain` 硬过滤并返回带领域元数据的切片，整个过程不依赖 MCP 内的大模型分类。
