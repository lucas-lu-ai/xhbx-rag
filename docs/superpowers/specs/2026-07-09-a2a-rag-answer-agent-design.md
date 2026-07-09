# A2A RAG 问答智能体暴露设计

日期：2026-07-09

## 背景

当前 `xhbx-rag` 已经具备完整的知识问答链路：query understanding、检索过滤参数生成、embedding、Milvus 聚合召回、BM25 关键词召回、RRF 融合、rerank、基于证据的回答生成和 citation 输出。

外部主控智能体由 AgentScope 框架开发，负责意图识别和 query 改写。主控需要把 query 委托给本项目的知识问答智能体，由本项目继续完成过滤参数生成、检索、排序和最终回答，然后通过对方 agent-runtime 约定的 A2A 协议返回。

对方接入文档采用旧式 A2A 任务接口：

- AgentCard：`GET /a2a/{agent_code}/.well-known/agent.json`
- 调用入口：`POST /a2a/{agent_code}`
- 同步方法：JSON-RPC `tasks/send`
- 流式方法：JSON-RPC `tasks/sendSubscribe`

本设计优先兼容对方主控平台，而不是只实现新版 `message/send` 形态。

## 范围

第一版包含：

- 暴露一个固定问答智能体：`xhbx-rag-answer`。
- 提供 AgentCard endpoint：`GET /a2a/xhbx-rag-answer/.well-known/agent.json`。
- 提供 JSON-RPC endpoint：`POST /a2a/xhbx-rag-answer`。
- 支持同步 `tasks/send`。
- 从 `params.message.parts[]` 中提取文本 query。
- 接收并回传 `params.sessionId`；未传时生成服务端 session id。
- 接收 `params.metadata` 中的 `traceparent`、`user_id`、`tenant_no`、`parent_session_code`，第一版只在响应 `metadata` 中回传，不改变检索行为。
- 内部调用现有 `answer_question(...)`，由现有问答链路完成过滤参数生成、检索、排序和回答。
- 将最终答案放入 `Task.status.message.parts[].text`。
- 将 `citations`、`evidence_count`、`retrieval_evidences` 放入 `Task.metadata`，便于主控做结构化处理。

第一版不包含：

- `tasks/sendSubscribe` 流式返回。
- `<<<DPB>>>`、`<<<SMB>>>`、`<<<NRB>>>` 等主子协议标记。
- 新版 A2A `message/send` 或 `message/stream`。
- Nacos 自动注册。
- 外部鉴权、API key 或签名校验。
- 多轮会话记忆注入检索 query。
- 暴露入库、批量执行、bad case、source reveal 等能力。

## 推荐方案

采用独立 FastAPI router：`src/xhbx_rag/web/a2a_routes.py`。

这个方案的取舍：

- 优点：贴合对方 agent-runtime 的固定路径和 JSON-RPC 方法；复用现有 Web 问答服务的资源构造与安全错误处理；不影响现有 `/api/answer`、`/api/answer/stream` 和 MCP 服务。
- 缺点：第一版只兼容旧式 A2A `tasks/send`，不是完整 A2A 通用 server。

未采用的方案：

- 同时支持新旧 A2A：范围更大，当前主控不需要。
- 只暴露普通 HTTP API：实现最简单，但不满足主控通过 A2A 委托的目标。

## 接口设计

### AgentCard

`GET /a2a/xhbx-rag-answer/.well-known/agent.json`

返回示例：

```json
{
  "name": "xhbx-rag-answer",
  "description": "保险销售知识库问答智能体，接收主控传入的 query，完成检索、排序、证据约束回答和引用返回。",
  "url": "http://<host>/a2a/xhbx-rag-answer",
  "version": "1.0.0",
  "capabilities": {
    "streaming": false
  },
  "skills": [
    {
      "id": "rag_qa",
      "name": "RAG 知识问答",
      "description": "基于保险绩优案例和培训课程知识库回答销售相关问题，并返回引用证据。"
    }
  ]
}
```

`url` 默认从当前请求推导，也允许后续通过环境变量覆盖公开 base URL。

### 同步问答

`POST /a2a/xhbx-rag-answer`

请求：

```json
{
  "jsonrpc": "2.0",
  "method": "tasks/send",
  "params": {
    "id": "task-001",
    "sessionId": "session-abc",
    "message": {
      "role": "user",
      "parts": [
        {
          "type": "text",
          "text": "客户说每年不能超过80万怎么办？"
        }
      ]
    },
    "metadata": {
      "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
      "user_id": "10001",
      "tenant_no": "product",
      "parent_session_code": "uuid-parent"
    }
  },
  "id": 1
}
```

成功响应：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "id": "task-001",
    "sessionId": "session-abc",
    "status": {
      "state": "completed",
      "message": {
        "role": "agent",
        "parts": [
          {
            "type": "text",
            "text": "可以先承接客户的年度预算边界，再引导客户看保障缺口、缴费期和家庭责任。"
          }
        ]
      }
    },
    "metadata": {
      "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
      "user_id": "10001",
      "tenant_no": "product",
      "parent_session_code": "uuid-parent",
      "evidence_count": 3,
      "citations": [],
      "retrieval_evidences": []
    }
  }
}
```

如果 `params.id` 为空，服务端生成 task id 并回传。`sessionId` 同理。

## 数据流

1. 主控智能体完成意图识别和 query 改写。
2. 主控以 JSON-RPC `tasks/send` 调用 `/a2a/xhbx-rag-answer`。
3. A2A route 校验 `jsonrpc`、`method`、`params.message.parts`。
4. 服务从 text parts 中拼接出 query，并校验非空。
5. 服务调用 `answer_question(query=query, top_n=20, top_k=5)`。
6. 现有问答链路完成过滤参数生成、检索、排序和证据约束回答。
7. A2A route 将回答映射为 A2A Task。
8. 主控收到 completed Task，从 `status.message.parts[].text` 读取最终答案，从 `metadata` 读取引用和证据。

第一版不会把 A2A `sessionId` 注入现有 RAG 链路。`xhbx-rag` 当前问答是单轮知识问答，session 只作为跨系统追踪字段保留。

## 错误处理

返回 JSON-RPC error，不返回 Python traceback、绝对路径或密钥内容。

错误码：

- `-32600`：JSON-RPC 请求结构非法，例如缺少 `jsonrpc` 或 `id`。
- `-32601`：不支持的方法，例如 `tasks/sendSubscribe`。
- `-32602`：参数非法，例如 query 为空、message parts 不含 text。
- `-32000`：问答服务暂时不可用，复用现有安全错误白名单。

示例：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32602,
    "message": "问题不能为空"
  }
}
```

对现有 `answer_question(...)` 抛出的安全业务错误按当前 Web 口径映射：

- 本地 Milvus Lite 索引占用：返回 `-32000`，message 使用现有安全文案。
- 配置缺失或参数错误：返回安全文案。
- 未知异常：统一返回 `问答服务暂时不可用`。

## 配置与部署

第一版复用现有 Web 服务进程和 Docker compose 的 `api` 服务，不新增独立进程。

内网调用示例：

```yaml
agent_code: xhbx-rag-answer
agent_type: A2A
name: 新华保险知识问答
url: http://xhbx-rag-api:8000/a2a/xhbx-rag-answer
```

当前用户明确允许私有服务器集群内不使用 HTTPS。第一版不做鉴权，但该 endpoint 只能绑定在可信内网或由集群网关限制访问来源。后续如需最小鉴权，可在 A2A route 上增加 shared token 校验，不影响协议主体。

## 测试

按 TDD 增加 `tests/test_a2a_routes.py` 或扩展 `tests/test_web_app.py`：

- AgentCard endpoint 返回固定 agent 信息、`url` 和 `capabilities.streaming=false`。
- `tasks/send` 会从 text parts 中提取 query 并调用注入的 fake `answer_question`。
- 成功响应符合 JSON-RPC 和 Task 结构，答案位于 `status.message.parts[].text`。
- `citations`、`evidence_count`、`retrieval_evidences` 被放入 `metadata`。
- 请求未传 `sessionId` 或 task id 时服务端会生成并回传。
- 空 query 返回 `-32602`。
- 不支持的 `tasks/sendSubscribe` 返回 `-32601`。
- 内部未知异常返回安全错误，不泄漏路径、堆栈或密钥。

## 后续扩展

- 支持 `tasks/sendSubscribe`，把现有 `/api/answer/stream` 的 SSE 事件映射为 A2A 流式事件。
- 支持平台主子协议标记输出或透传。
- 支持新版 A2A `message/send` 与 `message/stream`。
- 支持 shared token、mTLS 或网关鉴权。
- 支持 Nacos 注册，注册名使用 `xhbx-rag-answer`。
- 支持通过环境变量配置 `agent_code`、AgentCard `url`、默认 `top_n/top_k`。
