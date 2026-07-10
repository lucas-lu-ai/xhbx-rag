import json
import logging
from contextlib import contextmanager

import httpx
import pytest

from xhbx_rag.answer import (
    AnswerAgent,
    IncompleteModelOutputError,
    _retry_messages,
    answer_from_search_result,
)
from xhbx_rag.observability import MemoryTraceSink


def _delta_chunk(**delta: object) -> str:
    return json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


def _finish_chunk(reason: str) -> str:
    return json.dumps(
        {"choices": [{"delta": {}, "finish_reason": reason}]},
        ensure_ascii=False,
    )


def _sse_lines(*data_items: str) -> list[str]:
    lines = [f"data: {item}" for item in data_items]
    lines.append("data: [DONE]")
    return lines


def _answer_sse_lines(
    answer: str,
    citation_indexes: list[int],
    *,
    reasoning_parts: tuple[str, ...] = ("先梳理证据，", "再归纳回答要点。"),
) -> list[str]:
    content = json.dumps(
        {"answer": answer, "citation_indexes": citation_indexes},
        ensure_ascii=False,
    )
    middle = len(content) // 2
    return _sse_lines(
        *[_delta_chunk(reasoning_content=part) for part in reasoning_parts],
        _delta_chunk(content=content[:middle]),
        _delta_chunk(content=content[middle:]),
    )


class _FakeStreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code

    def iter_lines(self):
        yield from self._lines


class _InterruptingStreamResponse(_FakeStreamResponse):
    def iter_lines(self):
        partial = '{"answer":"部分正文'
        yield f"data: {_delta_chunk(content=partial)}"
        raise httpx.RemoteProtocolError("peer closed stream")


class _ReadErrorBeforeDeltaResponse(_FakeStreamResponse):
    def iter_lines(self):
        raise httpx.ReadError("socket reset before first delta")


class _ReadErrorAfterPartialContentResponse(_FakeStreamResponse):
    def iter_lines(self):
        partial = '{"answer":"ReadError 前的部分正文'
        yield f"data: {_delta_chunk(content=partial)}"
        raise httpx.ReadError("socket reset after partial content")


class _FakeSseClient:
    def __init__(
        self,
        lines: list[str],
        failures: list[Exception] | None = None,
        status_code: int = 200,
    ) -> None:
        self.calls: list[dict] = []
        self._lines = lines
        self._failures = list(failures or [])
        self._status_code = status_code

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
        if self._failures:
            raise self._failures.pop(0)
        yield _FakeStreamResponse(self._lines, self._status_code)


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


class _SequencedResponseClient(_FakeSseClient):
    def __init__(self, responses: list[_FakeStreamResponse]) -> None:
        super().__init__([])
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
        yield self._responses[response_index]


def _search_result() -> dict:
    return {
        "original_query": "保单整理对客户有什么作用？",
        "rewritten_query": "保单整理对客户的作用和价值是什么？",
        "intent": "general_sales_qa",
        "filters": {"chunk_types": []},
        "results": [
            {
                "chunk_id": "c1",
                "chunk_type": "customer_journey",
                "text": "客户可以直观看到保障缺口，激发解决意愿。",
                "score": 0.4,
                "rerank_score": 0.99,
                "metadata": {"stage": "需求唤醒与缺口发现"},
                "citations": [
                    {
                        "filename": "第3节.docx",
                        "section_name": "5.1 流程",
                        "source_id": "",
                        "quote": "用问话引导客户看表格，使其自行发现问题并产生解决意愿。",
                        "source_excerpt": "客户自己看表后说，原来这里还有这么大的缺口。",
                    }
                ],
            },
            {
                "chunk_id": "c2",
                "chunk_type": "strategy",
                "text": "保单整理是看得见的专业服务，能快速获得客户认同。",
                "score": 0.5,
                "rerank_score": 0.98,
                "metadata": {"strategy_name": "服务前置与专业替代推销策略"},
                "citations": [
                    {
                        "filename": "第1节.docx",
                        "section_name": "2.1 展示专业形象",
                        "source_id": "",
                        "quote": "保单整理是一种看得见的专业服务，能快速获得客户认同",
                    }
                ],
            },
        ],
    }


def _agent(http: _FakeSseClient, **kwargs: object) -> AnswerAgent:
    return AnswerAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="chat-model",
        http_client=http,  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def test_answer_agent_streams_thinking_and_parses_json_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="xhbx_rag.answer")
    http = _FakeSseClient(
        _answer_sse_lines(
            "保单整理能帮助客户看清保障缺口，并建立对代理人的专业信任。",
            [1, 2],
        )
    )
    thinking: list[str] = []
    agent = _agent(http, on_thinking_delta=thinking.append)

    result = agent.generate(_search_result())

    assert result.answer == "保单整理能帮助客户看清保障缺口，并建立对代理人的专业信任。"
    assert result.citation_indexes == [1, 2]
    assert result.reasoning == "先梳理证据，再归纳回答要点。"
    # 思考增量按到达顺序实时回调，而不是收尾时一次性给出。
    assert thinking == ["先梳理证据，", "再归纳回答要点。"]
    assert "正在重新生成" not in result.reasoning
    assert not [
        record for record in caplog.records if record.name == "xhbx_rag.answer"
    ]
    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.example.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer secret"
    body = call["json"]
    assert body["model"] == "chat-model"
    assert body["stream"] is True
    assert body["enable_thinking"] is True
    assert body["response_format"] == {"type": "json_object"}
    assert len(http.calls) == 1
    user_content = body["messages"][-1]["content"]
    assert "保单整理对客户有什么作用？" in user_content
    assert "[证据1]" in user_content
    assert "[引用1]" in user_content
    assert "原文：客户自己看表后说，原来这里还有这么大的缺口。" in user_content


def test_answer_agent_can_disable_thinking() -> None:
    http = _FakeSseClient(
        _answer_sse_lines("保单整理能帮助客户看清保障缺口。", [1], reasoning_parts=())
    )
    agent = _agent(http, enable_thinking=False)

    result = agent.generate(_search_result())

    assert http.calls[0]["json"]["enable_thinking"] is False
    assert result.reasoning == ""


def test_answer_agent_retries_stream_open_failures() -> None:
    http = _FakeSseClient(
        _answer_sse_lines("保单整理能帮助客户看清保障缺口。", [1]),
        failures=[
            httpx.RemoteProtocolError("Server disconnected without sending a response.")
        ],
    )
    agent = _agent(http, retry_base_delay=0)

    result = agent.generate(_search_result())

    assert result.answer == "保单整理能帮助客户看清保障缺口。"
    assert len(http.calls) == 2


def test_answer_agent_retries_read_error_before_any_delta() -> None:
    http = _SequencedResponseClient(
        [
            _ReadErrorBeforeDeltaResponse([]),
            _FakeStreamResponse(
                _answer_sse_lines(
                    "读取错误后连接重试成功。",
                    [1],
                    reasoning_parts=(),
                )
            ),
        ]
    )

    result = _agent(http, retry_base_delay=0).generate(_search_result())

    assert result.answer == "读取错误后连接重试成功。"
    assert len(http.calls) == 2


def test_answer_agent_retries_read_error_after_partial_content() -> None:
    partial = '{"answer":"ReadError 前的部分正文'
    http = _SequencedResponseClient(
        [
            _ReadErrorAfterPartialContentResponse([]),
            _FakeStreamResponse(
                _answer_sse_lines(
                    "读取中断后内容纠错成功。",
                    [1],
                    reasoning_parts=(),
                )
            ),
        ]
    )

    result = _agent(http).generate(_search_result())

    assert result.answer == "读取中断后内容纠错成功。"
    assert len(http.calls) == 2
    assert http.calls[1]["json"]["messages"][-2] == {
        "role": "assistant",
        "content": partial,
    }
    assert "流式连接中断: ReadError" in (
        http.calls[1]["json"]["messages"][-1]["content"]
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


def test_answer_agent_retries_transport_interruption_after_partial_content() -> None:
    class _PartialThenValidClient(_SequencedFakeSseClient):
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
            if len(self.calls) == 1:
                yield _InterruptingStreamResponse([])
                return
            yield _FakeStreamResponse(
                _answer_sse_lines(
                    "中断后重新生成成功。",
                    [1],
                    reasoning_parts=(),
                )
            )

    http = _PartialThenValidClient([[]])

    result = _agent(http).generate(_search_result())

    assert result.answer == "中断后重新生成成功。"
    assert len(http.calls) == 2
    assert "部分正文" in http.calls[1]["json"]["messages"][-2]["content"]
    assert "流式连接中断: RemoteProtocolError" in (
        http.calls[1]["json"]["messages"][-1]["content"]
    )


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
    base_messages = http.calls[0]["json"]["messages"]
    retry_messages = http.calls[1]["json"]["messages"]
    assert retry_messages[:2] == base_messages
    assert retry_messages[-2] == {"role": "assistant", "content": invalid}
    assert retry_messages[-1]["role"] == "user"
    correction = retry_messages[-1]["content"]
    assert "JSONDecodeError" in correction
    assert "请从头重新生成完整 JSON，不要续写上一次内容" in correction
    assert "answer 必须是非空字符串" in correction
    assert "citation_indexes 必须是整数数组" in correction


def test_answer_agent_reports_correction_retry_in_reasoning_and_safe_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="xhbx_rag.answer")
    invalid = '{"answer":"MODEL-OUTPUT-SENTINEL'
    first_thinking = "第一次思考。"
    retry_notice = "\n\n[第 1 次模型输出不完整，正在重新生成。]\n\n"
    second_thinking = "第二次思考。"
    http = _SequencedFakeSseClient(
        [
            _sse_lines(
                _delta_chunk(reasoning_content=first_thinking),
                _delta_chunk(content=invalid),
            ),
            _answer_sse_lines(
                "第二次生成成功。",
                [1],
                reasoning_parts=(second_thinking,),
            ),
        ]
    )
    thinking: list[str] = []

    result = _agent(http, on_thinking_delta=thinking.append).generate(_search_result())

    assert thinking == [first_thinking, retry_notice, second_thinking]
    assert result.reasoning == "".join(thinking)
    retry_records = [
        record for record in caplog.records if record.name == "xhbx_rag.answer"
    ]
    assert len(retry_records) == 1
    log_message = retry_records[0].getMessage()
    assert "attempt=1" in log_message
    assert "error_type=JSONDecodeError" in log_message
    assert f"content_chars={len(invalid)}" in log_message
    assert "saw_done=True" in log_message
    assert "finish_reason=None" in log_message
    assert "MODEL-OUTPUT-SENTINEL" not in caplog.text


def test_answer_agent_sanitizes_unknown_finish_reason_in_retry_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="xhbx_rag.answer")
    content = '{"answer":"首次回答","citation_indexes":[1]}'
    http = _SequencedFakeSseClient(
        [
            _sse_lines(
                _delta_chunk(content=content),
                _finish_chunk("MODEL-OUTPUT-SENTINEL\nINJECTED"),
            ),
            _answer_sse_lines("第二次生成成功。", [1], reasoning_parts=()),
        ]
    )

    result = _agent(http).generate(_search_result())

    assert result.answer == "第二次生成成功。"
    retry_records = [
        record for record in caplog.records if record.name == "xhbx_rag.answer"
    ]
    assert len(retry_records) == 1
    log_message = retry_records[0].getMessage()
    assert "attempt=1" in log_message
    assert "error_type=ValueError" in log_message
    assert f"content_chars={len(content)}" in log_message
    assert "saw_done=True" in log_message
    assert "finish_reason=other" in log_message
    assert "MODEL-OUTPUT-SENTINEL" not in caplog.text
    assert "INJECTED" not in caplog.text
    assert "\nINJECTED" not in caplog.text


def test_answer_agent_sanitizes_unknown_finish_reason_in_final_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="xhbx_rag.answer")
    content = '{"answer":"有效但终态异常","citation_indexes":[1]}'
    responses = [
        _sse_lines(
            _delta_chunk(content=content),
            _finish_chunk("MODEL-OUTPUT-SENTINEL\nINJECTED"),
        )
        for _ in range(3)
    ]
    http = _SequencedFakeSseClient(responses)

    with pytest.raises(IncompleteModelOutputError) as exc_info:
        _agent(http).generate(_search_result())

    assert isinstance(exc_info.value.__cause__, ValueError)
    exception_texts = [str(exc_info.value), str(exc_info.value.__cause__)]
    for text in exception_texts:
        assert "finish_reason=other" in text
        assert "MODEL-OUTPUT-SENTINEL" not in text
        assert "INJECTED" not in text
    correction_messages = [
        call["json"]["messages"][-1]["content"] for call in http.calls[1:]
    ]
    assert all("finish_reason=other" in message for message in correction_messages)
    assert all(
        "MODEL-OUTPUT-SENTINEL" not in message and "INJECTED" not in message
        for message in correction_messages
    )
    retry_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "xhbx_rag.answer"
    ]
    assert len(retry_messages) == 2
    assert all("finish_reason=other" in message for message in retry_messages)
    assert "MODEL-OUTPUT-SENTINEL" not in caplog.text
    assert "INJECTED" not in caplog.text


def test_answer_agent_stops_after_three_invalid_outputs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="xhbx_rag.answer")
    invalid = '{"answer":"仍未闭合'
    http = _SequencedFakeSseClient(
        [
            _sse_lines(
                _delta_chunk(reasoning_content=f"第{attempt}次失败思考。"),
                _delta_chunk(content=invalid),
            )
            for attempt in range(1, 4)
        ]
    )
    thinking: list[str] = []
    agent = _agent(http, on_thinking_delta=thinking.append)

    with pytest.raises(
        IncompleteModelOutputError,
        match="模型输出不完整，已尝试 3 次: JSONDecodeError",
    ) as exc_info:
        agent.generate(_search_result())

    assert len(http.calls) == 3
    assert exc_info.value.last_error.startswith("JSONDecodeError:")
    assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)
    assert thinking == [
        "第1次失败思考。",
        "\n\n[第 1 次模型输出不完整，正在重新生成。]\n\n",
        "第2次失败思考。",
        "\n\n[第 2 次模型输出不完整，正在重新生成。]\n\n",
        "第3次失败思考。",
    ]
    assert "第 3 次模型输出不完整，正在重新生成" not in "".join(thinking)
    retry_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "xhbx_rag.answer"
    ]
    assert len(retry_messages) == 2
    assert "attempt=1" in retry_messages[0]
    assert "attempt=2" in retry_messages[1]


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


def test_answer_agent_can_succeed_on_third_attempt_with_only_latest_failure() -> None:
    first_invalid = '{"answer":"第一次未闭合-FIRST'
    second_invalid = '{"answer":"第二次仍未闭合-SECOND'
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
    third_messages = http.calls[2]["json"]["messages"]
    assert len(third_messages) == 4
    assert third_messages[-2]["content"] == second_invalid
    assert first_invalid not in str(third_messages)


def test_retry_messages_bound_output_excerpt_and_error_summary() -> None:
    invalid = "HEAD" + "x" * 5_000 + "TAIL"
    messages = _retry_messages(
        [{"role": "system", "content": "base"}],
        invalid_content=invalid,
        error=ValueError("ERROR-HEAD" + "y" * 1_000 + "ERROR-TAIL"),
    )

    excerpt = messages[-2]["content"]
    assert len(excerpt) <= 4_000
    assert excerpt.startswith("HEAD")
    assert excerpt.endswith("TAIL")
    assert "已截断" in excerpt

    correction = messages[-1]["content"]
    error_summary = correction.split("错误：", 1)[1].split("\n", 1)[0]
    assert len(error_summary) <= 800
    assert error_summary.startswith("ValueError: ERROR-HEAD")
    assert error_summary.endswith("ERROR-TAIL")
    assert "已截断" in error_summary


def test_answer_agent_parses_fenced_json_content() -> None:
    content = '```json\n{"answer":"稳健的沟通建议。","citation_indexes":[1]}\n```'
    http = _FakeSseClient(_sse_lines(_delta_chunk(content=content)))
    agent = _agent(http)

    result = agent.generate(_search_result())

    assert result.answer == "稳健的沟通建议。"
    assert result.citation_indexes == [1]


def test_answer_from_search_result_maps_selected_citations_and_emits_trace() -> None:
    class _FakeAnswerAgent:
        def generate(self, search_result: dict):
            assert search_result["original_query"] == "保单整理对客户有什么作用？"
            return type(
                "Answer",
                (),
                {
                    "answer": "保单整理能帮助客户发现保障缺口[引用2]，并建立专业信任。[引用1]",
                    "citation_indexes": [2, 99, 2, 1],
                },
            )()

    trace = MemoryTraceSink()

    result = answer_from_search_result(
        _search_result(),
        answer_agent=_FakeAnswerAgent(),
        trace=trace,
    )

    assert result["answer"] == "保单整理能帮助客户发现保障缺口，并建立专业信任。"
    assert result["evidence_count"] == 2
    assert [citation["filename"] for citation in result["citations"]] == [
        "第1节.docx",
        "第3节.docx",
    ]
    assert [citation["selected"] for citation in result["citations"]] == [True, True]
    assert [citation["evidence_index"] for citation in result["citations"]] == [2, 1]
    assert [event.step for event in trace.events] == ["answer.generated"]
    assert trace.events[0].payload["citation_count"] == 2


def test_answer_from_search_result_supplements_underselected_citations_with_evidence_sources() -> None:
    class _UnderselectingAnswerAgent:
        def generate(self, search_result: dict):
            return type(
                "Answer",
                (),
                {
                    "answer": "保单整理能帮助客户发现保障缺口，并建立专业信任。",
                    "citation_indexes": [1],
                },
            )()

    result = answer_from_search_result(
        _search_result(),
        answer_agent=_UnderselectingAnswerAgent(),
    )

    assert [citation["filename"] for citation in result["citations"]] == [
        "第3节.docx",
        "第1节.docx",
    ]
    # 模型选中的引用与兜底补齐的引用要能区分，且都能挂回对应证据。
    assert [citation["selected"] for citation in result["citations"]] == [True, False]
    assert [citation["evidence_index"] for citation in result["citations"]] == [1, 2]


def test_answer_from_search_result_returns_reasoning_in_payload() -> None:
    class _ThinkingAnswerAgent:
        def generate(self, search_result: dict):
            return type(
                "Answer",
                (),
                {
                    "answer": "稳健的沟通建议。",
                    "citation_indexes": [1],
                    "reasoning": "先梳理证据，再归纳回答要点。",
                },
            )()

    result = answer_from_search_result(
        _search_result(),
        answer_agent=_ThinkingAnswerAgent(),
    )

    assert result["reasoning"] == "先梳理证据，再归纳回答要点。"


def _answer_sse_client() -> _FakeSseClient:
    return _FakeSseClient(_answer_sse_lines("稳健的沟通建议。", [1]))


def test_answer_agent_injects_compliance_guidance_for_risky_evidence() -> None:
    http = _answer_sse_client()
    agent = _agent(http)
    search_result = _search_result()
    search_result["results"][0]["metadata"]["compliance_risks"] = [
        "收益承诺风险",
        "理赔承诺风险",
    ]

    agent.generate(search_result)

    user_content = http.calls[0]["json"]["messages"][-1]["content"]
    assert "合规注意" in user_content
    assert "保证收益" in user_content
    assert "承诺一定赔付" in user_content


def test_answer_agent_omits_compliance_block_without_risky_evidence() -> None:
    http = _answer_sse_client()
    agent = _agent(http)

    agent.generate(_search_result())

    user_content = http.calls[0]["json"]["messages"][-1]["content"]
    assert "合规注意" not in user_content


def test_answer_from_search_result_traces_compliance_risks() -> None:
    class _FakeAnswerAgent:
        def generate(self, search_result: dict):
            return type(
                "Answer",
                (),
                {"answer": "稳健的沟通建议。", "citation_indexes": [1]},
            )()

    search_result = _search_result()
    search_result["results"][0]["metadata"]["compliance_risks"] = ["收益承诺风险"]
    search_result["results"][1]["metadata"]["compliance_risks"] = [
        "收益承诺风险",
        "适当性风险",
    ]
    trace = MemoryTraceSink()

    answer_from_search_result(
        search_result,
        answer_agent=_FakeAnswerAgent(),
        trace=trace,
    )

    payload = trace.events[0].payload
    # 跨证据去重且保持首次出现顺序。
    assert payload["compliance_risks"] == ["收益承诺风险", "适当性风险"]


def test_answer_from_search_result_skips_model_when_no_results() -> None:
    class _FailingAnswerAgent:
        def generate(self, search_result: dict):
            raise AssertionError("不应该在无证据时调用回答模型")

    search_result = {
        "original_query": "无关问题",
        "rewritten_query": "",
        "intent": "out_of_scope",
        "filters": {},
        "results": [],
    }

    result = answer_from_search_result(search_result, answer_agent=_FailingAnswerAgent())

    assert result["answer"] == "当前检索结果不足以确认。"
    assert result["citations"] == []
    assert result["evidence_count"] == 0
    assert result["reasoning"] == ""
