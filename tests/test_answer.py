import json
from contextlib import contextmanager

import httpx

from xhbx_rag.answer import AnswerAgent, answer_from_search_result
from xhbx_rag.observability import MemoryTraceSink


def _delta_chunk(**delta: object) -> str:
    return json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


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


def test_answer_agent_streams_thinking_and_parses_json_response() -> None:
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
    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.example.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer secret"
    body = call["json"]
    assert body["model"] == "chat-model"
    assert body["stream"] is True
    assert body["enable_thinking"] is True
    assert body["response_format"] == {"type": "json_object"}
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
