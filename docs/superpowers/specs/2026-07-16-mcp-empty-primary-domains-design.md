# MCP 空领域全库检索契约设计

日期：2026-07-16

## 1. 背景

当前默认 MCP 工具 `kb_search_knowledge` 已删除知识库 ID 参数，并要求服务器问答智能体通过必填参数 `primaryDomains` 指定七类一级领域。现有实现把空数组视为参数错误，并建议无法分类时传入全部七类。

服务器问答智能体还会遇到无法匹配七类体系的问题。此时展开为全部七类并不等价于真正全库检索，因为它会排除 `primary_domain` 缺失或异常的历史数据。因此需要为必填参数增加明确的空数组语义。

本设计是 `docs/superpowers/specs/2026-07-16-mcp-primary-domain-retrieval-design.md` 的增量变更，覆盖其中关于“空数组非法”和“无法判断时传全部七类”的要求；其他契约保持不变。

## 2. 目标

- `primaryDomains` 继续作为 `kb_search_knowledge` 的必填参数。
- `primaryDomains=[]` 表示不施加一级领域过滤，查询统一 collection 中的所有文档。
- 真正全库检索包含七类已标注文档，以及 `primary_domain` 缺失、为空或异常的历史文档。
- 非空数组继续按七类一级领域做确定性的硬过滤。
- 服务器问答智能体能匹配体系时传一个或多个领域，无法匹配体系时传空数组。
- 保持 MCP 不调用大模型、不自动推断领域、不按 `source_kind` 过滤。

## 3. 非目标

- 不把 `primaryDomains` 改为可选参数。
- 不把空数组自动展开为全部七类。
- 不在 MCP 内调用 chat/completions 或 `infer_query_domains`。
- 不修改 Milvus collection schema。
- 不重新切片、不重新生成 embedding、不重新入库。
- 不改变 `knowledgeTypes`、`retrievalMode`、`hybridWeights`、`topK` 和 `includeDetails` 的现有行为。

## 4. 对外参数契约

工具签名保持：

```python
kb_search_knowledge(
    query: str,
    primaryDomains: PrimaryDomainsInput,
    knowledgeTypes: list[str] | None = None,
    retrievalMode: str = "HYBRID",
    hybridWeights: dict[str, Any] | None = None,
    topK: int = 10,
    includeDetails: bool = False,
) -> dict[str, Any]
```

`tools/list` 必须继续把 `query` 和 `primaryDomains` 列在 `required` 中。`primaryDomains` 的 JSON Schema 为数组，元素枚举七个合法一级领域，`minItems=0`、`maxItems=7`。

合法调用分为两类：

```json
{
  "query": "客户担心保费太高怎么沟通",
  "primaryDomains": ["销售技能", "客户经营"]
}
```

```json
{
  "query": "这个问题无法匹配现有七类体系",
  "primaryDomains": []
}
```

省略 `primaryDomains` 仍由 MCP 参数模型拒绝。空数组不是省略参数，而是调用方显式请求全库检索。

## 5. 校验规则

MCP 业务函数继续负责领域值校验，以便非法值返回统一 `McpResponse`。

规则如下：

- 参数必须是数组；
- 数组允许 0 至 7 项；
- 每项必须是字符串；
- 去除每项首尾空白；
- 非空项只能是七个固定一级领域；
- 自动去重，并按调用方首次出现顺序保留；
- 空数组直接返回空的规范化领域列表。

非数组、非字符串项、未知分类或超过七项返回：

```json
{
  "success": false,
  "data": null,
  "errorCode": "10004",
  "errorMessage": "参数错误: primaryDomains 必须是由 0 到 7 个合法一级领域组成的数组"
}
```

## 6. 检索数据流

### 6.1 非空领域数组

输入：

```json
{"primaryDomains": ["销售技能", "客户经营"]}
```

MCP 构造：

```python
filters = {"primary_domains": ["销售技能", "客户经营"]}
```

Milvus 过滤表达式：

```text
primary_domain in ["销售技能", "客户经营"]
```

向量召回和关键词召回都在相同领域范围内执行。

### 6.2 空领域数组

输入：

```json
{"primaryDomains": []}
```

MCP 构造：

```python
filters = {}
```

不生成 `primary_domain` 表达式。向量召回和关键词召回都查询统一 collection 的全部文档，包含未标注或异常领域的历史数据。

MCP 必须在自身边界完成空数组到空过滤对象的转换，不能依赖 Milvus store 恰好忽略 `{"primary_domains": []}` 的实现细节。

## 7. 服务器问答智能体职责

服务器问答智能体调用规则改为：

> 调用 kb_search_knowledge 时，primaryDomains 必须传入。能够匹配一级体系时，从产品知识、合规与风控、销售技能、客户经营、行业与公司、个人成长、组织发展中选择一个或多个最相关领域；无法匹配现有体系时传入空数组 []，由 MCP 查询全部文档。不得创造体系外分类，不得省略 primaryDomains。

`primaryDomains=[]` 只用于无法匹配现有体系的情况，不是每次检索的默认值。能够判断领域时应传非空数组，以利用领域过滤减少无关召回。

## 8. 返回与错误行为

空数组只改变召回范围，不改变返回结构：

- 精简模式继续返回 `docId`、`knowledgeType`、`title`、`content`、`primaryDomain`、`domainTags`；
- 完整模式继续在结果顶层返回 `primaryDomain`、`domainTags`；
- 未标注文档允许返回 `primaryDomain=""` 和 `domainTags=[]`；
- 无召回结果仍成功返回空 `data`；
- 内部异常仍返回安全化的 `500`。

## 9. 脚本与文档

`scripts/test_mcp.sh` 继续通过 `PRIMARY_DOMAINS_JSON` 接收数组。默认 smoke 值可保留为 `["销售技能"]`，同时在文档中增加空数组全库检索示例：

```bash
PRIMARY_DOMAINS_JSON='[]' scripts/test_mcp.sh "无法匹配现有体系的问题"
```

同步更新：

- `README.md`；
- `docs/知识库 MCP Tool 文档.md`；
- `docs/私有化部署资源文档.md`；
- MCP server instructions 与工具 description；
- `.env.mcp.example` 中的必传参数说明。

## 10. 测试与验收

必须覆盖：

1. `tools/list` 仍把 `primaryDomains` 标记为必填；
2. schema 允许 0 至 7 项；
3. 空数组规范化成功，并调用检索器时传 `filters={}`；
4. 空数组不会提前返回空结果，向量与关键词链路都会执行；
5. 非空数组继续生成 `primary_domains` 过滤；
6. 重复领域继续去重并保留首次顺序；
7. 非数组、非字符串、未知领域和超过七项继续返回包装后的 `10004`；
8. 省略必填参数继续产生 MCP 参数错误；
9. 工具 description 明确“能匹配传一个或多个，无法匹配传空数组”；
10. 精简与完整结果结构不变；
11. smoke 脚本能够用 `PRIMARY_DOMAINS_JSON='[]'` 构造合法调用；
12. 全量测试通过。

验收标准：服务器问答智能体始终显式传入 `primaryDomains`；非空数组在七类体系内硬过滤，空数组不生成领域过滤并查询统一 collection 的真正全量文档。
