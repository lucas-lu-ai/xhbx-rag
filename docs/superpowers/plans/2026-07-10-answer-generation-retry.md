# 回答生成纠错重试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当回答模型返回不完整或不符合模式的 JSON 时，把上一次错误反馈给模型并重新生成，总共最多 3 次，最终失败时向用户返回安全、明确的模型输出不完整提示。

**Architecture:** `AnswerAgent.generate()` 管理最多 3 次的内容生成循环；`_stream_chat_content()` 返回正文和流完成元数据，严格识别提前结束、长度截断和损坏 SSE；专门异常通过 Web 共用安全错误映射转换成固定用户文案。内容重试与现有连接级重试分开计数，不进行本地 JSON 修补。

**Tech Stack:** Python 3.12、httpx SSE、Pydantic v2、FastAPI、pytest

---

## 文件结构

- Modify: `src/xhbx_rag/answer.py` — 流完成元数据、输出校验、纠错消息、最多 3 次内容生成和专门异常。
- Modify: `tests/test_answer.py` — 回答生成重试、反馈消息、流异常和思考内容的单元测试。
- Modify: `src/xhbx_rag/web/safe_errors.py` — 专门异常到固定安全文案的共用映射。
- Modify: `src/xhbx_rag/web/app.py` — 非流式 REST 路由复用共用安全错误映射。
- Modify: `tests/test_web_app.py` — REST 与 SSE 用户可见错误回归测试。

### Task 1: JSON 与模式校验失败时纠错重试

**Files:**
- Modify: `tests/test_answer.py`
- Modify: `src/xhbx_rag/answer.py:17-22, 94-142`

- [ ] **Step 1: 写入“第二次成功”和“最多三次”失败测试**

在 `tests/test_answer.py` 中导入 `pytest` 和新异常，并添加可按请求次序返回不同 SSE 的测试客户端：

```python
import pytest

from xhbx_rag.answer import (
    AnswerAgent,
    IncompleteModelOutputError,
    answer_from_search_result,
)


class _SequencedFakeSseClient(_FakeSseClient):
    def __init__(self, responses: list[list[str]]) -> None:
        super().__init__(responses[0])
        self._responses = responses

    @contextmanager
    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: dict,
        json: dict,
        timeout: float,
    ):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        response_index = min(len(self.calls) - 1, len(self._responses) - 1)
        yield _FakeStreamResponse(self._responses[response_index])


def test_answer_agent_retries_invalid_json_with_error_feedback() -> None:
    invalid = '{"answer":"未闭合的回答'
    http = _SequencedFakeSseClient(
        [
            _sse_lines(_delta_chunk(content=invalid)),
            _answer_sse_lines("第二次生成成功。", [1], reasoning_parts=()),
        ]
    )
    agent = _agent(http)

    result = agent.generate(_search_result())

    assert result.answer == "第二次生成成功。"
    assert len(http.calls) == 2
    retry_messages = http.calls[1]["json"]["messages"]
    assert retry_messages[-2] == {"role": "assistant", "content": invalid}
    assert retry_messages[-1]["role"] == "user"
    assert "JSONDecodeError" in retry_messages[-1]["content"]
    assert "从头重新生成完整 JSON" in retry_messages[-1]["content"]


def test_answer_agent_stops_after_three_invalid_outputs() -> None:
    invalid = '{"answer":"仍未闭合'
    http = _SequencedFakeSseClient(
        [[*_sse_lines(_delta_chunk(content=invalid))]] * 3
    )
    agent = _agent(http)

    with pytest.raises(
        IncompleteModelOutputError,
        match="模型输出不完整，已尝试 3 次",
    ):
        agent.generate(_search_result())

    assert len(http.calls) == 3


def test_answer_agent_retries_schema_validation_failure() -> None:
    invalid = '{"answer":"   ","citation_indexes":[1]}'
    http = _SequencedFakeSseClient(
        [
            _sse_lines(_delta_chunk(content=invalid)),
            _answer_sse_lines("结构已纠正。", [1], reasoning_parts=()),
        ]
    )

    result = _agent(http).generate(_search_result())

    assert result.answer == "结构已纠正。"
    assert "ValidationError" in http.calls[1]["json"]["messages"][-1]["content"]


def test_answer_agent_can_succeed_on_third_attempt() -> None:
    first_invalid = '{"answer":"第一次未闭合'
    second_invalid = '{"answer":"第二次仍未闭合'
    http = _SequencedFakeSseClient(
        [
            _sse_lines(_delta_chunk(content=first_invalid)),
            _sse_lines(_delta_chunk(content=second_invalid)),
            _answer_sse_lines("第三次生成成功。", [1], reasoning_parts=()),
        ]
    )

    result = _agent(http).generate(_search_result())

    assert result.answer == "第三次生成成功。"
    assert len(http.calls) == 3
    assert http.calls[2]["json"]["messages"][-2]["content"] == second_invalid
```

- [ ] **Step 2: 运行测试并确认按预期失败**

Run:

```bash
uv run pytest \
  tests/test_answer.py::test_answer_agent_retries_invalid_json_with_error_feedback \
  tests/test_answer.py::test_answer_agent_stops_after_three_invalid_outputs \
  tests/test_answer.py::test_answer_agent_retries_schema_validation_failure \
  tests/test_answer.py::test_answer_agent_can_succeed_on_third_attempt -q
```

Expected: FAIL，原因是 `IncompleteModelOutputError` 尚不存在，且 `generate()` 尚未进行内容纠错重试。

- [ ] **Step 3: 实现专门异常、纠错消息和三次生成循环**

在 `src/xhbx_rag/answer.py` 中加入常量与异常：

```python
MODEL_OUTPUT_ATTEMPTS = 3
INVALID_OUTPUT_EXCERPT_CHARS = 4_000


class IncompleteModelOutputError(AnswerGenerationError):
    """Raised after all model output correction attempts are exhausted."""

    def __init__(self, last_error: str) -> None:
        self.last_error = last_error
        super().__init__(
            f"模型输出不完整，已尝试 {MODEL_OUTPUT_ATTEMPTS} 次: {last_error}"
        )
```

加入有界反馈助手。错误文本和输出摘录分别限制长度，避免重试提示词无界增长：

```python
def _bounded_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    return f"{text[:head]}\n...（已截断）...\n{text[-tail:]}"


def _retry_messages(
    base_messages: list[dict[str, str]],
    *,
    invalid_content: str,
    error: Exception,
) -> list[dict[str, str]]:
    error_summary = _bounded_text(
        f"{type(error).__name__}: {error}",
        800,
    )
    invalid_excerpt = _bounded_text(
        invalid_content,
        INVALID_OUTPUT_EXCERPT_CHARS,
    )
    return [
        *base_messages,
        {"role": "assistant", "content": invalid_excerpt},
        {
            "role": "user",
            "content": (
                "上一次输出无法作为完整回答解析。\n"
                f"错误：{error_summary}\n"
                "请从头重新生成完整 JSON，不要续写上一次内容。"
                "只能输出包含 answer 和 citation_indexes 字段的 JSON object；"
                "answer 必须是非空字符串，citation_indexes 必须是整数数组。"
            ),
        },
    ]
```

把 `generate()` 改为最多 3 次的严格解析循环。每一轮从独立 `content` 开始，成功后立即返回；失败时仅反馈最近一次输出：

```python
def generate(self, search_result: dict[str, Any]) -> GeneratedAnswer:
    base_messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(search_result)},
    ]
    messages = base_messages
    thinking_parts: list[str] = []
    last_error = "未知模型输出错误"

    for attempt in range(1, MODEL_OUTPUT_ATTEMPTS + 1):
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "stream": True,
            "enable_thinking": self.enable_thinking,
            "response_format": {"type": "json_object"},
        }
        content = self._stream_chat_content(body, thinking_parts)
        try:
            data = json.loads(_strip_json_fences(content))
            generated = GeneratedAnswer.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - model output is normalized below
            last_error = _bounded_text(f"{type(exc).__name__}: {exc}", 800)
            if attempt == MODEL_OUTPUT_ATTEMPTS:
                raise IncompleteModelOutputError(last_error) from exc
            messages = _retry_messages(
                base_messages,
                invalid_content=content,
                error=exc,
            )
            continue
        return generated.model_copy(update={"reasoning": "".join(thinking_parts)})

    raise IncompleteModelOutputError(last_error)
```

- [ ] **Step 4: 运行新增测试并确认通过**

Run:

```bash
uv run pytest \
  tests/test_answer.py::test_answer_agent_retries_invalid_json_with_error_feedback \
  tests/test_answer.py::test_answer_agent_stops_after_three_invalid_outputs \
  tests/test_answer.py::test_answer_agent_retries_schema_validation_failure \
  tests/test_answer.py::test_answer_agent_can_succeed_on_third_attempt -q
```

Expected: `4 passed`。

- [ ] **Step 5: 运行回答模块回归测试**

Run: `uv run pytest tests/test_answer.py -q`

Expected: 全部通过；现有合法 JSON 只发起一次请求，现有连接打开失败重试行为保持不变。

- [ ] **Step 6: 提交内容纠错重试**

```bash
git add src/xhbx_rag/answer.py tests/test_answer.py
git commit -m "fix: retry incomplete answer generation"
```

### Task 2: 识别流提前结束、长度截断和损坏 SSE

**Files:**
- Modify: `tests/test_answer.py`
- Modify: `src/xhbx_rag/answer.py:84-93, 144-216`

- [ ] **Step 1: 写入三个流完整性失败测试**

在 `tests/test_answer.py` 中增加带 `finish_reason` 的辅助函数与参数化测试。每种第一次失败都必须触发第二次完整生成：

```python
def _finish_chunk(reason: str) -> str:
    return json.dumps(
        {"choices": [{"delta": {}, "finish_reason": reason}]},
        ensure_ascii=False,
    )


@pytest.mark.parametrize(
    ("first_response", "expected_error"),
    [
        (
            [_delta_chunk(content='{"answer":"流提前结束')],
            "未收到 [DONE]",
        ),
        (
            _sse_lines(
                _delta_chunk(content='{"answer":"长度截断'),
                _finish_chunk("length"),
            ),
            "finish_reason=length",
        ),
        (
            _sse_lines(
                _delta_chunk(content='{"answer":"已收到部分'),
                "{not-json",
            ),
            "SSE 数据块解析失败",
        ),
    ],
)
def test_answer_agent_retries_incomplete_streams(
    first_response: list[str],
    expected_error: str,
) -> None:
    http = _SequencedFakeSseClient(
        [
            [
                line if line.startswith("data:") else f"data: {line}"
                for line in first_response
            ],
            _answer_sse_lines("流重试成功。", [1], reasoning_parts=()),
        ]
    )

    result = _agent(http).generate(_search_result())

    assert result.answer == "流重试成功。"
    assert len(http.calls) == 2
    assert expected_error in http.calls[1]["json"]["messages"][-1]["content"]
```

增加一个连接在收到部分正文后中断的测试客户端，验证部分内容也进入下一次纠错消息：

```python
class _InterruptingStreamResponse(_FakeStreamResponse):
    def iter_lines(self):
        partial = '{"answer":"部分正文'
        yield f"data: {_delta_chunk(content=partial)}"
        raise httpx.RemoteProtocolError("peer closed stream")


def test_answer_agent_retries_transport_interruption_after_partial_content() -> None:
    class _PartialThenValidClient(_SequencedFakeSseClient):
        @contextmanager
        def stream(self, method, url, *, headers, json, timeout):
            self.calls.append(
                {
                    "method": method,
                    "url": url,
                    "headers": headers,
                    "json": json,
                    "timeout": timeout,
                }
            )
            if len(self.calls) == 1:
                yield _InterruptingStreamResponse([])
                return
            yield _FakeStreamResponse(
                _answer_sse_lines("中断后重新生成成功。", [1], reasoning_parts=())
            )

    http = _PartialThenValidClient([[]])

    result = _agent(http).generate(_search_result())

    assert result.answer == "中断后重新生成成功。"
    assert "部分正文" in http.calls[1]["json"]["messages"][-2]["content"]
```

- [ ] **Step 2: 运行流完整性测试并确认失败**

Run:

```bash
uv run pytest \
  tests/test_answer.py::test_answer_agent_retries_incomplete_streams \
  tests/test_answer.py::test_answer_agent_retries_transport_interruption_after_partial_content -q
```

Expected: FAIL。当前实现把未收到 `[DONE]` 的内容交给 JSON 解析、不读取 `finish_reason`、静默丢弃坏块，并在部分传输中断时直接抛出 `RemoteProtocolError`。

- [ ] **Step 3: 增加流结果类型和严格 SSE 解析**

在 `src/xhbx_rag/answer.py` 中导入 `dataclass`，并定义单次流结果：

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class _StreamChatResult:
    content: str
    saw_done: bool
    finish_reason: str | None = None
    stream_error: str | None = None
```

把 `_chunk_delta()` 替换为同时返回 delta 和结束原因的严格解析函数。合法的 usage 块可以没有 choices；只有 JSON 本身损坏才抛出：

```python
def _chunk_event(data: str) -> tuple[dict[str, Any], str | None]:
    payload = json.loads(data)
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        return {}, None
    choice = choices[0]
    if not isinstance(choice, dict):
        return {}, None
    delta = choice.get("delta")
    finish_reason = choice.get("finish_reason")
    return (
        delta if isinstance(delta, dict) else {},
        finish_reason if isinstance(finish_reason, str) else None,
    )
```

把 `_stream_chat_content()` 的返回值改为 `_StreamChatResult`。关键行为如下：

```python
def _stream_chat_content(
    self,
    body: dict[str, Any],
    thinking_parts: list[str],
) -> _StreamChatResult:
    attempts = max(1, self.retry_attempts)
    for attempt in range(1, attempts + 1):
        content_parts: list[str] = []
        received_delta = False
        saw_done = False
        finish_reason: str | None = None
        try:
            with self.http_client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.timeout,
            ) as response:
                status_code = int(getattr(response, "status_code", 200))
                if status_code >= 400:
                    raise _StreamStatusError(status_code)
                for raw_line in response.iter_lines():
                    data = _sse_data(raw_line)
                    if data is None:
                        continue
                    if data == "[DONE]":
                        saw_done = True
                        break
                    try:
                        delta, chunk_finish_reason = _chunk_event(data)
                    except ValueError as exc:
                        return _StreamChatResult(
                            content="".join(content_parts),
                            saw_done=False,
                            finish_reason=finish_reason,
                            stream_error=f"SSE 数据块解析失败: {exc}",
                        )
                    if chunk_finish_reason is not None:
                        finish_reason = chunk_finish_reason
                    reasoning = delta.get("reasoning_content")
                    if isinstance(reasoning, str) and reasoning:
                        received_delta = True
                        thinking_parts.append(reasoning)
                        if self.on_thinking_delta is not None:
                            self.on_thinking_delta(reasoning)
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        received_delta = True
                        content_parts.append(text)
            return _StreamChatResult(
                content="".join(content_parts),
                saw_done=saw_done,
                finish_reason=finish_reason,
            )
        except _StreamStatusError as exc:
            if attempt == attempts or not is_retryable_status_code(exc.status_code):
                raise AnswerGenerationError(str(exc)) from exc
        except RETRYABLE_TRANSPORT_ERRORS as exc:
            if received_delta:
                return _StreamChatResult(
                    content="".join(content_parts),
                    saw_done=False,
                    finish_reason=finish_reason,
                    stream_error=f"流式连接中断: {type(exc).__name__}",
                )
            if attempt == attempts:
                raise
        sleep_before_retry(attempt, self.retry_base_delay)
    raise AnswerGenerationError("chat/completions 流式重试次数耗尽")
```

- [ ] **Step 4: 在生成循环中校验流完成状态**

加入流校验助手：

```python
def _require_complete_stream(result: _StreamChatResult) -> None:
    if result.stream_error:
        raise ValueError(result.stream_error)
    if not result.saw_done:
        raise ValueError("流式响应未收到 [DONE]")
    if result.finish_reason not in (None, "stop"):
        raise ValueError(f"流式响应异常结束: finish_reason={result.finish_reason}")
```

在 `generate()` 的每轮循环中将正文读取与解析改为以下完整逻辑：

```python
attempt_thinking_parts: list[str] = []
stream_result = self._stream_chat_content(body, attempt_thinking_parts)
thinking_parts.extend(attempt_thinking_parts)
content = stream_result.content
try:
    _require_complete_stream(stream_result)
    data = json.loads(_strip_json_fences(content))
    generated = GeneratedAnswer.model_validate(data)
except Exception as exc:  # noqa: BLE001 - model output is normalized below
    last_error = _bounded_text(f"{type(exc).__name__}: {exc}", 800)
    if attempt == MODEL_OUTPUT_ATTEMPTS:
        raise IncompleteModelOutputError(last_error) from exc
    messages = _retry_messages(
        base_messages,
        invalid_content=content,
        error=exc,
    )
    continue
return generated.model_copy(update={"reasoning": "".join(thinking_parts)})
```

每轮使用新的 `attempt_thinking_parts`，再按顺序合并到总 `thinking_parts`，确保正文绝不跨轮拼接。

- [ ] **Step 5: 运行新增流测试并确认通过**

Run:

```bash
uv run pytest \
  tests/test_answer.py::test_answer_agent_retries_incomplete_streams \
  tests/test_answer.py::test_answer_agent_retries_transport_interruption_after_partial_content -q
```

Expected: 参数化的 3 个流场景和部分中断场景全部通过。

- [ ] **Step 6: 运行回答模块回归测试**

Run: `uv run pytest tests/test_answer.py -q`

Expected: 全部通过，无警告或错误日志。

- [ ] **Step 7: 提交流完整性识别**

```bash
git add src/xhbx_rag/answer.py tests/test_answer.py
git commit -m "fix: detect incomplete answer streams"
```

### Task 3: 重试思考提示与安全诊断日志

**Files:**
- Modify: `tests/test_answer.py`
- Modify: `src/xhbx_rag/answer.py:1-15, 123-193`

- [ ] **Step 1: 写入思考重试提示与日志脱敏测试**

```python
def test_answer_agent_reports_retry_without_logging_model_content(caplog) -> None:
    invalid = '{"answer":"SENSITIVE_MODEL_OUTPUT'
    http = _SequencedFakeSseClient(
        [
            _sse_lines(
                _delta_chunk(reasoning_content="第一次分析。"),
                _delta_chunk(content=invalid),
            ),
            _answer_sse_lines(
                "重试成功。",
                [1],
                reasoning_parts=("第二次分析。",),
            ),
        ]
    )
    visible_thinking: list[str] = []
    agent = _agent(http, on_thinking_delta=visible_thinking.append)

    with caplog.at_level("WARNING", logger="xhbx_rag.answer"):
        result = agent.generate(_search_result())

    assert visible_thinking == [
        "第一次分析。",
        "\n\n[第 1 次模型输出不完整，正在重新生成。]\n\n",
        "第二次分析。",
    ]
    assert result.reasoning == "".join(visible_thinking)
    assert "attempt=1" in caplog.text
    assert "content_chars=" in caplog.text
    assert "SENSITIVE_MODEL_OUTPUT" not in caplog.text
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_answer.py::test_answer_agent_reports_retry_without_logging_model_content -q`

Expected: FAIL，当前没有重试状态思考增量和结构化警告日志。

- [ ] **Step 3: 实现状态增量和脱敏日志**

在 `src/xhbx_rag/answer.py` 中加入模块 logger：

```python
import logging

logger = logging.getLogger(__name__)
```

每次可重试内容失败时记录不含原文的字段，并把状态提示同时写入最终 reasoning 与实时回调：

```python
retry_notice = f"\n\n[第 {attempt} 次模型输出不完整，正在重新生成。]\n\n"
logger.warning(
    "Answer generation retry attempt=%s error_type=%s content_chars=%s "
    "saw_done=%s finish_reason=%s",
    attempt,
    type(exc).__name__,
    len(content),
    stream_result.saw_done,
    stream_result.finish_reason,
)
thinking_parts.append(retry_notice)
if self.on_thinking_delta is not None:
    self.on_thinking_delta(retry_notice)
```

失败轮与成功轮都先把 `attempt_thinking_parts` 合并进总缓冲；只在确实还有下一次生成时追加 `retry_notice`。

- [ ] **Step 4: 运行测试并确认通过**

Run: `uv run pytest tests/test_answer.py::test_answer_agent_reports_retry_without_logging_model_content -q`

Expected: `1 passed`，日志不包含模型原文。

- [ ] **Step 5: 运行回答模块测试并提交**

Run: `uv run pytest tests/test_answer.py -q`

Expected: 全部通过。

```bash
git add src/xhbx_rag/answer.py tests/test_answer.py
git commit -m "feat: report answer correction retries"
```

### Task 4: 向 REST、SSE、批量和 A2A 暴露统一安全文案

**Files:**
- Modify: `tests/test_web_app.py`
- Modify: `src/xhbx_rag/web/safe_errors.py:1-48`
- Modify: `src/xhbx_rag/web/app.py:246-267`

- [ ] **Step 1: 写入 REST 和 SSE 安全文案测试**

在 `tests/test_web_app.py` 中导入专门异常，并添加：

```python
from xhbx_rag.answer import IncompleteModelOutputError


def test_answer_route_exposes_safe_incomplete_model_output_error(monkeypatch) -> None:
    def fail_answer(*, query, top_n, top_k):
        raise IncompleteModelOutputError(
            "JSONDecodeError: secret model output at /Users/milan/private"
        )

    monkeypatch.setattr(web_app, "answer_question", fail_answer)
    client = TestClient(web_app.create_app())

    response = client.post(
        "/api/answer",
        json={"query": "保单整理有什么作用？", "top_n": 20, "top_k": 5},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "模型输出不完整，已尝试 3 次，请稍后重试。"
    )
    assert "secret model output" not in response.text
    assert "/Users/milan" not in response.text


def test_answer_stream_exposes_safe_incomplete_model_output_error(monkeypatch) -> None:
    def fake_events(*, query, top_n, top_k):
        yield {
            "type": "_exception",
            "exception": IncompleteModelOutputError(
                "JSONDecodeError: secret model output"
            ),
        }

    monkeypatch.setattr(
        web_app,
        "answer_question_stream_events",
        fake_events,
    )
    client = TestClient(web_app.create_app())

    with client.stream(
        "POST",
        "/api/answer/stream",
        json={"query": "保单整理有什么作用？", "top_n": 20, "top_k": 5},
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    assert "模型输出不完整，已尝试 3 次，请稍后重试。" in body
    assert "secret model output" not in body
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
uv run pytest \
  tests/test_web_app.py::test_answer_route_exposes_safe_incomplete_model_output_error \
  tests/test_web_app.py::test_answer_stream_exposes_safe_incomplete_model_output_error -q
```

Expected: FAIL，两个入口仍返回“问答服务暂时不可用”。

- [ ] **Step 3: 在共用安全错误映射中加入固定文案**

修改 `src/xhbx_rag/web/safe_errors.py`：

```python
from ..answer import IncompleteModelOutputError
from .services import LOCAL_INDEX_UNAVAILABLE_ERROR, REQUIRED_CONFIG_KEYS

INCOMPLETE_MODEL_OUTPUT_ERROR_DETAIL = (
    "模型输出不完整，已尝试 3 次，请稍后重试。"
)


def answer_exception_detail(exc: Exception) -> str:
    """把问答异常归一为安全中文文案；未知异常一律返回兜底文案。"""
    if isinstance(exc, IncompleteModelOutputError):
        return INCOMPLETE_MODEL_OUTPUT_ERROR_DETAIL
    if isinstance(exc, ValueError):
        message = str(exc)
        if message == LOCAL_INDEX_UNAVAILABLE_ERROR:
            return message
        if is_safe_answer_error(message):
            return message
    return UNAVAILABLE_ANSWER_ERROR_DETAIL
```

- [ ] **Step 4: 让非流式 REST 路由复用共用映射**

修改 `src/xhbx_rag/web/app.py` 的通用异常分支；状态码保持 502，只替换 detail 的来源：

```python
except Exception as exc:  # noqa: BLE001 - API boundary returns safe summary
    logger.exception("Answer route failed")
    raise HTTPException(
        status_code=502,
        detail=_answer_exception_detail(exc),
    ) from exc
```

SSE 已经调用 `_answer_exception_detail(exc)`，无需修改其数据流。批量执行和 A2A 也已经导入同一个 `answer_exception_detail()`，因此会自动获得相同固定文案。

- [ ] **Step 5: 运行新增 Web 测试并确认通过**

Run:

```bash
uv run pytest \
  tests/test_web_app.py::test_answer_route_exposes_safe_incomplete_model_output_error \
  tests/test_web_app.py::test_answer_stream_exposes_safe_incomplete_model_output_error -q
```

Expected: `2 passed`。

- [ ] **Step 6: 运行安全错误相关回归测试**

Run:

```bash
uv run pytest \
  tests/test_web_app.py \
  tests/test_web_batch_runner.py \
  tests/test_a2a_routes.py -q
```

Expected: 全部通过；未知异常仍返回“问答服务暂时不可用”，内部路径和错误原文不泄漏。

- [ ] **Step 7: 提交安全错误映射**

```bash
git add \
  src/xhbx_rag/web/safe_errors.py \
  src/xhbx_rag/web/app.py \
  tests/test_web_app.py
git commit -m "fix: expose safe incomplete model error"
```

### Task 5: 完整验证与交付检查

**Files:**
- Verify: `src/xhbx_rag/answer.py`
- Verify: `src/xhbx_rag/web/safe_errors.py`
- Verify: `src/xhbx_rag/web/app.py`
- Verify: `tests/test_answer.py`
- Verify: `tests/test_web_app.py`

- [ ] **Step 1: 运行语法与差异检查**

项目当前只配置 pytest，没有配置 lint 依赖；使用 Python 编译检查和 Git 空白错误检查：

```bash
uv run python -m compileall -q src tests
git diff --check
```

Expected: 两条命令均退出码为 0，且无错误输出。

- [ ] **Step 2: 运行针对性测试**

Run:

```bash
uv run pytest \
  tests/test_answer.py \
  tests/test_web_app.py \
  tests/test_web_services.py \
  tests/test_web_batch_runner.py \
  tests/test_a2a_routes.py -q
```

Expected: 全部通过，无失败、错误或意外警告。

- [ ] **Step 3: 运行完整测试套件**

Run: `uv run pytest -q`

Expected: 全部通过。

- [ ] **Step 4: 检查最终差异和工作区**

```bash
git diff --check
git status --short
git log -5 --oneline
```

Expected: `git diff --check` 无输出；只有明确属于本计划且尚未提交的文件才能出现在状态列表中；提交历史包含本计划的四个实现提交。

- [ ] **Step 5: 如格式化产生必要改动，验证后提交**

仅当检查工具确实修改了本计划文件时执行：

```bash
git add \
  src/xhbx_rag/answer.py \
  src/xhbx_rag/web/safe_errors.py \
  src/xhbx_rag/web/app.py \
  tests/test_answer.py \
  tests/test_web_app.py
git commit -m "style: format answer retry changes"
```

再次运行 Task 5 Step 1-3，确认最终提交对应的代码仍全部通过。
