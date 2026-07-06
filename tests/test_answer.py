import httpx

from xhbx_rag.answer import AnswerAgent, answer_from_search_result
from xhbx_rag.observability import MemoryTraceSink


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict, timeout: float) -> _FakeResponse:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        return _FakeResponse(self.payload)


class _FlakyHttpClient:
    def __init__(self, failures: list[Exception], payload: dict) -> None:
        self.failures = failures
        self.payload = payload
        self.calls: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict, timeout: float) -> _FakeResponse:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        if self.failures:
            raise self.failures.pop(0)
        return _FakeResponse(self.payload)


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


def test_answer_agent_posts_evidence_context_and_parses_json_response() -> None:
    http = _FakeHttpClient(
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"answer":"保单整理能帮助客户看清保障缺口，并建立对代理人的专业信任。",'
                            '"citation_indexes":[1,2]}'
                        )
                    }
                }
            ]
        }
    )
    agent = AnswerAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="chat-model",
        http_client=http,
    )

    result = agent.generate(_search_result())

    assert result.answer == "保单整理能帮助客户看清保障缺口，并建立对代理人的专业信任。"
    assert result.citation_indexes == [1, 2]
    call = http.calls[0]
    assert call["url"] == "https://api.example.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer secret"
    assert call["json"]["model"] == "chat-model"
    assert call["json"]["response_format"] == {"type": "json_object"}
    user_content = call["json"]["messages"][-1]["content"]
    assert "保单整理对客户有什么作用？" in user_content
    assert "[证据1]" in user_content
    assert "[引用1]" in user_content
    assert "原文：客户自己看表后说，原来这里还有这么大的缺口。" in user_content


def test_answer_agent_retries_transient_http_errors() -> None:
    http = _FlakyHttpClient(
        failures=[httpx.RemoteProtocolError("Server disconnected without sending a response.")],
        payload={
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"answer":"保单整理能帮助客户看清保障缺口。",'
                            '"citation_indexes":[1]}'
                        )
                    }
                }
            ]
        },
    )
    agent = AnswerAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="chat-model",
        http_client=http,
        retry_base_delay=0,
    )

    result = agent.generate(_search_result())

    assert result.answer == "保单整理能帮助客户看清保障缺口。"
    assert len(http.calls) == 2


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


def _answer_http_client() -> _FakeHttpClient:
    return _FakeHttpClient(
        {
            "choices": [
                {
                    "message": {
                        "content": '{"answer":"稳健的沟通建议。","citation_indexes":[1]}'
                    }
                }
            ]
        }
    )


def test_answer_agent_injects_compliance_guidance_for_risky_evidence() -> None:
    http = _answer_http_client()
    agent = AnswerAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="chat-model",
        http_client=http,
    )
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
    http = _answer_http_client()
    agent = AnswerAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="chat-model",
        http_client=http,
    )

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
