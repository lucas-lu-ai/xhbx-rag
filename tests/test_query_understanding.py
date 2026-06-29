import pytest

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
