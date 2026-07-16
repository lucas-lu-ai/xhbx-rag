import pytest
import httpx

from xhbx_rag.query_understanding import (
    QueryUnderstanding,
    QueryUnderstandingAgent,
    QueryUnderstandingError,
)


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


def test_query_understanding_validates_rewritten_query_when_retrieval_needed() -> None:
    with pytest.raises(ValueError, match="rewritten_query"):
        QueryUnderstanding.model_validate(
            {
                "intent": "script_search",
                "rewritten_query": "",
                "needs_retrieval": True,
                "filters": {"chunk_types": ["script"]},
            }
        )


def test_query_understanding_normalizes_filter_string_fields() -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": "script_search",
            "rewritten_query": "客户抗拒谈保险时如何开场",
            "needs_retrieval": True,
            "filters": {
                "chunk_types": ["script"],
                "stage": " 售前 ",
                "scenario": None,
                "objection": 123,
                "strategy_names": [],
            },
        }
    )

    assert result.filters.stage == "售前"
    assert result.filters.scenario == ""
    assert result.filters.objection == "123"


def test_query_understanding_drops_unknown_chunk_types() -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": "general_sales_qa",
            "rewritten_query": "保单整理对客户的作用和价值是什么？",
            "needs_retrieval": True,
            "filters": {
                "chunk_types": ["qa", "strategy", "customer_journey", ""],
                "stage": "",
                "strategy_names": ["保单整理"],
            },
        }
    )

    assert result.filters.chunk_types == ["strategy", "customer_journey"]


def test_query_understanding_accepts_training_course_chunk_type() -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": "general_sales_qa",
            "rewritten_query": "促成课程的标准讲法是什么？",
            "needs_retrieval": True,
            "filters": {"chunk_types": ["training_course"]},
        }
    )

    assert result.filters.chunk_types == ["training_course"]


def test_query_understanding_accepts_knowledge_entry_chunk_type() -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": "general_sales_qa",
            "rewritten_query": "培训课件中的产品责任是什么？",
            "needs_retrieval": True,
            "collection_targets": ["course"],
            "filters": {"chunk_types": ["knowledge_entry"]},
        }
    )

    assert result.filters.chunk_types == ["knowledge_entry"]


@pytest.mark.parametrize(
    ("collection_targets", "expected"),
    [
        (["case"], ["case"]),
        (["course"], ["course"]),
        (["course", "case", "course"], ["course", "case"]),
        ([], ["case", "course"]),
        (None, ["case", "course"]),
        (["unknown", ""], ["case", "course"]),
        (["unknown", "case", "unknown"], ["case"]),
    ],
)
def test_query_understanding_normalizes_collection_targets(
    collection_targets: list[str] | None, expected: list[str]
) -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": "general_sales_qa",
            "rewritten_query": "保险销售案例和标准课程怎么讲？",
            "needs_retrieval": True,
            "collection_targets": collection_targets,
            "filters": {},
        }
    )

    assert result.collection_targets == expected


def test_query_understanding_defaults_missing_collection_targets_to_all() -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": "general_sales_qa",
            "rewritten_query": "保险销售案例和标准课程怎么讲？",
            "needs_retrieval": True,
            "filters": {},
        }
    )

    assert result.collection_targets == ["case", "course"]


@pytest.mark.parametrize(
    ("intent", "chunk_type"),
    [
        ("journey_search", "customer_journey"),
        ("strategy_search", "strategy"),
        ("script_search", "script"),
        ("objection_handling", "objection_handling"),
    ],
)
def test_query_understanding_falls_back_to_all_when_case_semantics_miss_case_target(
    intent: str,
    chunk_type: str,
) -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": intent,
            "rewritten_query": "查询案例实战经验",
            "needs_retrieval": True,
            "collection_targets": ["course"],
            "filters": {"chunk_types": [chunk_type]},
        }
    )

    assert result.collection_targets == ["case", "course"]


@pytest.mark.parametrize(
    "intent",
    ["journey_search", "objection_handling"],
)
def test_query_understanding_uses_case_only_intent_as_routing_guard_when_chunk_types_empty(
    intent: str,
) -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": intent,
            "rewritten_query": "查询案例实战经验",
            "needs_retrieval": True,
            "collection_targets": ["course"],
            "filters": {"chunk_types": []},
        }
    )

    assert result.collection_targets == ["case", "course"]


@pytest.mark.parametrize(
    ("intent", "chunk_types"),
    [
        ("script_search", ["training_course"]),
        ("script_search", []),
        ("strategy_search", ["training_course"]),
        ("strategy_search", []),
    ],
)
def test_query_understanding_preserves_course_target_for_ambiguous_intent(
    intent: str,
    chunk_types: list[str],
) -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": intent,
            "rewritten_query": "查询制式话术或标准流程",
            "needs_retrieval": True,
            "collection_targets": ["course"],
            "filters": {"chunk_types": chunk_types},
        }
    )

    assert result.collection_targets == ["course"]


def test_query_understanding_falls_back_to_all_when_course_semantics_miss_course_target() -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": "general_sales_qa",
            "rewritten_query": "查询培训课程",
            "needs_retrieval": True,
            "collection_targets": ["case"],
            "filters": {"chunk_types": ["training_course"]},
        }
    )

    assert result.collection_targets == ["case", "course"]


@pytest.mark.parametrize("collection_targets", [["case"], ["course"]])
def test_query_understanding_falls_back_to_all_for_mixed_chunk_types_with_one_target(
    collection_targets: list[str],
) -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": "general_sales_qa",
            "rewritten_query": "比较实战话术与培训课程",
            "needs_retrieval": True,
            "collection_targets": collection_targets,
            "filters": {"chunk_types": ["script", "training_course"]},
        }
    )

    assert result.collection_targets == ["case", "course"]


@pytest.mark.parametrize(
    ("intent", "chunk_types", "collection_targets"),
    [
        ("journey_search", ["customer_journey"], ["case"]),
        ("strategy_search", ["strategy"], ["case"]),
        ("script_search", ["script"], ["case"]),
        ("objection_handling", ["objection_handling"], ["case"]),
        ("general_sales_qa", ["training_course"], ["course"]),
    ],
)
def test_query_understanding_preserves_semantically_consistent_single_target(
    intent: str,
    chunk_types: list[str],
    collection_targets: list[str],
) -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": intent,
            "rewritten_query": "查询指定类型知识",
            "needs_retrieval": True,
            "collection_targets": collection_targets,
            "filters": {"chunk_types": chunk_types},
        }
    )

    assert result.collection_targets == collection_targets


@pytest.mark.parametrize("collection_targets", [["case"], ["course"]])
def test_query_understanding_preserves_general_qa_target_when_chunk_types_empty(
    collection_targets: list[str],
) -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": "general_sales_qa",
            "rewritten_query": "保险销售知识是什么？",
            "needs_retrieval": True,
            "collection_targets": collection_targets,
            "filters": {"chunk_types": []},
        }
    )

    assert result.collection_targets == collection_targets


def test_query_understanding_does_not_reconcile_targets_when_retrieval_not_needed() -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": "out_of_scope",
            "rewritten_query": "",
            "needs_retrieval": False,
            "collection_targets": ["course"],
            "filters": {"chunk_types": ["script"]},
        }
    )

    assert result.collection_targets == ["course"]


def test_query_understanding_does_not_reconcile_out_of_scope_targets() -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": "out_of_scope",
            "rewritten_query": "查询非保险销售知识",
            "needs_retrieval": True,
            "collection_targets": ["course"],
            "filters": {"chunk_types": ["script"]},
        }
    )

    assert result.collection_targets == ["course"]


def test_query_understanding_prompt_explains_collection_routing() -> None:
    from xhbx_rag.query_understanding import _SYSTEM_PROMPT

    assert "case 包含 customer_journey | strategy | script | objection_handling" in _SYSTEM_PROMPT
    assert "course 包含 training_course | knowledge_entry" in _SYSTEM_PROMPT
    assert '同时需要案例和课程时选择 ["case", "course"]' in _SYSTEM_PROMPT
    assert "collection_targets 必须与 intent 和 filters.chunk_types 一致" in _SYSTEM_PROMPT
    assert "script_search 和 strategy_search 本身不足以决定 collection_targets" in _SYSTEM_PROMPT
    assert "制式话术、标准流程可属于 course" in _SYSTEM_PROMPT
    assert '无法确定时选择 ["case", "course"]' in _SYSTEM_PROMPT
    assert "不代表两个物理 collection" in _SYSTEM_PROMPT


def test_query_understanding_normalizes_empty_filter_arrays_to_blank_strings() -> None:
    result = QueryUnderstanding.model_validate(
        {
            "intent": "strategy_search",
            "rewritten_query": "保单整理策略对客户的作用和价值是什么？",
            "needs_retrieval": True,
            "filters": {
                "chunk_types": [],
                "stage": [],
                "scenario": [],
                "objection": [],
                "strategy_names": ["保单整理"],
            },
        }
    )

    assert result.filters.stage == ""
    assert result.filters.scenario == ""
    assert result.filters.objection == ""


def test_query_understanding_agent_parses_openai_compatible_json_response() -> None:
    http = _FakeHttpClient(
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"intent":"script_search","rewritten_query":"客户抗拒谈保险时如何开场",'
                            '"needs_retrieval":true,"filters":{"chunk_types":["script"],"stage":"售前"}}'
                        )
                    }
                }
            ]
        }
    )
    agent = QueryUnderstandingAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="chat-model",
        http_client=http,
    )

    result = agent.understand("客户不想聊保险怎么开场？")

    assert result.intent == "script_search"
    assert result.rewritten_query == "客户抗拒谈保险时如何开场"
    assert result.filters.chunk_types == ["script"]
    call = http.calls[0]
    assert call["url"] == "https://api.example.com/v1/chat/completions"
    assert call["json"]["model"] == "chat-model"
    assert "客户不想聊保险怎么开场？" in call["json"]["messages"][-1]["content"]


def test_query_understanding_agent_retries_transient_http_errors() -> None:
    http = _FlakyHttpClient(
        failures=[httpx.RemoteProtocolError("Server disconnected without sending a response.")],
        payload={
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"intent":"script_search","rewritten_query":"客户抗拒谈保险时如何开场",'
                            '"needs_retrieval":true,"filters":{"chunk_types":["script"],"stage":"售前"}}'
                        )
                    }
                }
            ]
        },
    )
    agent = QueryUnderstandingAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="chat-model",
        http_client=http,
        retry_base_delay=0,
    )

    result = agent.understand("客户不想聊保险怎么开场？")

    assert result.rewritten_query == "客户抗拒谈保险时如何开场"
    assert len(http.calls) == 2


def test_query_understanding_agent_does_not_retry_read_error() -> None:
    http = _FlakyHttpClient(
        failures=[httpx.ReadError("socket reset")],
        payload={
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"intent":"script_search","rewritten_query":"不应到达",'
                            '"needs_retrieval":true,"filters":{}}'
                        )
                    }
                }
            ]
        },
    )
    agent = QueryUnderstandingAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="chat-model",
        http_client=http,
        retry_base_delay=0,
    )

    with pytest.raises(httpx.ReadError, match="socket reset"):
        agent.understand("客户不想聊保险怎么开场？")

    assert len(http.calls) == 1


def test_query_understanding_agent_fails_on_invalid_json() -> None:
    http = _FakeHttpClient({"choices": [{"message": {"content": "not-json"}}]})
    agent = QueryUnderstandingAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="chat-model",
        http_client=http,
    )

    with pytest.raises(QueryUnderstandingError):
        agent.understand("客户不想聊保险怎么开场？")
