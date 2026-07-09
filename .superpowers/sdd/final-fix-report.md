# A2A 边界修复报告

日期：2026-07-09

## 修复范围

只处理 final reviewer 指出的 3 个 A2A 边界问题，没有扩展其它能力：

1. Malformed JSON body 不再走 FastAPI 422，改为返回 HTTP 200 的 JSON-RPC error envelope。
2. `message.parts[].text` 改为严格要求字符串，非字符串直接返回 `-32602`，且不会调用 `answer_question`。
3. `params.id` 和 `params.sessionId` 改为只在缺失或 `None` 时生成 UUID；显式 `0` 会原样回传。

## 代码修改

- `src/xhbx_rag/web/a2a_routes.py`
  - `handle_jsonrpc` 改成接收 `Request`，手动 `await request.json()`。
  - 捕获 JSON 解析异常并返回 `{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"JSON-RPC 请求格式不合法"}}`。
  - `_extract_query` 取消 `str(...)` 强转，text part 的 `text` 必须是字符串。
  - 增加 `_normalize_task_identifier`，避免 `0` 被误判为缺失。

- `tests/test_a2a_routes.py`
  - 新增坏 JSON 测试。
  - 新增非字符串 text part 测试。
  - 新增 `id/sessionId=0` 保留测试。

## 验证

已执行并通过：

- `uv run pytest tests/test_a2a_routes.py -q`
- `uv run pytest tests/test_web_app.py -q`

