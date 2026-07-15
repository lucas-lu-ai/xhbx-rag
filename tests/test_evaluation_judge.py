from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import httpx
import pytest

from xhbx_rag.evaluation.config import EvaluationConfig
from xhbx_rag.evaluation.judge import (
    EvaluationJudgeAgent,
    JudgeEvaluationError,
)
from xhbx_rag.evaluation.models import ERROR_TAGS, EvaluationItem, GoldEvidence


API_KEY = "judge-secret-key"


class FakeResponse:
    def __init__(
        self,
        payload: object,
        *,
        status_error: httpx.HTTPStatusError | None = None,
    ) -> None:
        self.payload = payload
        self.status_error = status_error

    def raise_for_status(self) -> None:
        if self.status_error is not None:
            raise self.status_error

    def json(self) -> object:
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class FakeClient:
    def __init__(self, outcomes: Iterable[object]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []
        self.close_calls = 0

    def post(
        self,
        url: str,
        *,
        headers: dict,
        json: dict,
        timeout: float,
    ) -> FakeResponse:
        self.requests.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, FakeResponse)
        return outcome

    def close(self) -> None:
        self.close_calls += 1


def make_config(**overrides: object) -> EvaluationConfig:
    values: dict[str, object] = {
        "judge_base_url": "https://judge.example.com/v1///",
        "judge_api_key": API_KEY,
        "judge_model_name": "judge-model",
        "judge_timeout": 23.5,
        "judge_retry_attempts": 2,
        "same_model_judge": False,
    }
    values.update(overrides)
    return EvaluationConfig(**values)


def make_item() -> EvaluationItem:
    return EvaluationItem(
        item_id="row-2",
        excel_row=2,
        question="客户预算有限时应该如何沟通？",
        reference_answer="先确认客户预算，再讨论保障缺口。",
        trace_status="部分支持",
        primary_chunk_id="chunk-main",
        gold_chunk_ids=["chunk-main", "chunk-other"],
        gold_evidences=[
            GoldEvidence(
                chunk_id="chunk-main",
                source_path="案例/预算沟通.docx",
                locator="第3段",
                excerpt="先确认客户预算。",
                support_note="支持先了解预算。",
            )
        ],
    )


def make_answer() -> dict[str, object]:
    return {
        "answer": "先了解客户预算，并结合保障缺口给出建议。",
        "retrieval_evidences": [
            {
                "chunk_id": "chunk-main",
                "text": "先确认客户预算，再分析保障缺口。",
            }
        ],
        "citations": [
            {
                "evidence_index": 1,
                "source_path": "案例/预算沟通.docx",
                "quote": "先确认客户预算。",
            }
        ],
    }


def valid_judge_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "事实正确性得分": 30,
        "关键点覆盖得分": 18,
        "证据忠实性得分": 17,
        "相关性与表达得分": 9,
        "参考答案关键点": ["先确认客户预算", "讨论保障缺口"],
        "已覆盖关键点": ["先确认客户预算", "讨论保障缺口"],
        "缺失关键点": [],
        "无依据表述": [],
        "错误标签": [],
        "扣分原因": "回答覆盖主要观点，引用证据基本充分。",
        "改进建议": "可以进一步说明预算与保障缺口的关系。",
    }
    payload.update(overrides)
    return payload


def response_with_content(content: object) -> FakeResponse:
    return FakeResponse({"choices": [{"message": {"content": content}}]})


def valid_judge_response(**overrides: object) -> FakeResponse:
    return response_with_content(
        json.dumps(valid_judge_payload(**overrides), ensure_ascii=False)
    )


def test_judge_sends_expected_request_and_returns_chinese_result() -> None:
    client = FakeClient([valid_judge_response()])
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    result = agent.evaluate(make_item(), make_answer())

    request = client.requests[0]
    assert request["url"] == "https://judge.example.com/v1/chat/completions"
    assert request["headers"] == {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    assert request["timeout"] == 23.5
    assert request["json"]["model"] == "judge-model"
    assert request["json"]["temperature"] == 0
    assert request["json"]["response_format"] == {"type": "json_object"}
    assert result.correctness_score == 30
    assert result.reason == "回答覆盖主要观点，引用证据基本充分。"


def test_judge_prompts_define_scoring_boundary_and_include_evaluation_context() -> None:
    client = FakeClient([valid_judge_response()])
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    agent.evaluate(make_item(), make_answer())

    messages = client.requests[0]["json"]["messages"]
    system_prompt = messages[0]["content"]
    user_prompt = messages[1]["content"]
    assert "事实正确性" in system_prompt and "35" in system_prompt
    assert "关键点覆盖" in system_prompt and "20" in system_prompt
    assert "证据忠实性" in system_prompt and "20" in system_prompt
    assert "相关性与表达" in system_prompt and "10" in system_prompt
    assert "引用及黄金来源命中" in system_prompt and "15" in system_prompt
    assert "确定性规则" in system_prompt
    assert "不得" in system_prompt and "15" in system_prompt
    assert "参考答案中未被溯源证据支持的扩写不是自动真理" in system_prompt
    assert "不得因措辞不同扣减事实正确性得分" in system_prompt
    assert "简体中文" in system_prompt
    assert "只输出" in system_prompt and "JSON object" in system_prompt
    assert all(tag in system_prompt for tag in ERROR_TAGS)
    assert all(
        alias in system_prompt
        for alias in (
            "事实正确性得分",
            "关键点覆盖得分",
            "证据忠实性得分",
            "相关性与表达得分",
            "参考答案关键点",
            "已覆盖关键点",
            "缺失关键点",
            "无依据表述",
            "错误标签",
            "扣分原因",
            "改进建议",
        )
    )
    assert all(
        value in user_prompt
        for value in (
            "客户预算有限时应该如何沟通？",
            "先确认客户预算，再讨论保障缺口。",
            "部分支持",
            "chunk-main",
            "chunk-other",
            "先确认客户预算。",
            "先了解客户预算，并结合保障缺口给出建议。",
            "检索证据",
            "引用",
        )
    )
    assert "未溯源扩写不应被视为自动真理" in user_prompt
    assert "不同措辞不应扣减事实正确性得分" in user_prompt


def test_api_key_only_appears_in_authorization_and_not_in_repr_or_prompt() -> None:
    client = FakeClient([valid_judge_response()])
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    agent.evaluate(make_item(), make_answer())

    request = client.requests[0]
    assert API_KEY not in repr(make_config())
    assert API_KEY not in repr(agent)
    assert API_KEY not in request["url"]
    assert API_KEY not in json.dumps(request["json"], ensure_ascii=False)
    assert request["headers"]["Authorization"] == f"Bearer {API_KEY}"


def test_judge_accepts_markdown_json_fence() -> None:
    content = json.dumps(valid_judge_payload(), ensure_ascii=False)
    client = FakeClient([response_with_content(f"```json\n{content}\n```")])

    result = EvaluationJudgeAgent(
        make_config(), http_client=client
    ).evaluate(make_item(), make_answer())

    assert result.groundedness_score == 17


@pytest.mark.parametrize(
    "invalid_response",
    [
        FakeResponse({}),
        FakeResponse({"choices": []}),
        FakeResponse({"choices": [{}]}),
        FakeResponse({"choices": [{"message": {}}]}),
        response_with_content(123),
        response_with_content("not-json"),
        FakeResponse(json.JSONDecodeError("无效 JSON", "", 0)),
        response_with_content(
            json.dumps(valid_judge_payload(extra="非法字段"), ensure_ascii=False)
        ),
        valid_judge_response(**{"事实正确性得分": 36}),
        valid_judge_response(**{"错误标签": ["其他错误"]}),
    ],
)
def test_judge_retries_model_output_and_response_structure_errors(
    invalid_response: FakeResponse,
) -> None:
    client = FakeClient([invalid_response, valid_judge_response()])
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    result = agent.evaluate(make_item(), make_answer())

    assert result.correctness_score == 30
    assert len(client.requests) == 2
    repair_prompt = client.requests[1]["json"]["messages"][-1]["content"]
    assert "上一次输出无法通过评测结构校验" in repair_prompt


def test_judge_retry_attempts_are_retries_after_the_initial_attempt() -> None:
    client = FakeClient(
        [response_with_content("not-json"), response_with_content("still-bad")]
    )
    agent = EvaluationJudgeAgent(
        make_config(judge_retry_attempts=1), http_client=client
    )

    with pytest.raises(JudgeEvaluationError, match="裁判输出结构校验失败"):
        agent.evaluate(make_item(), make_answer())

    assert len(client.requests) == 2
    assert [request["timeout"] for request in client.requests] == [23.5, 23.5]


def test_judge_rejects_english_reason_after_all_retries() -> None:
    english_response = valid_judge_response(
        **{
            "扣分原因": "The answer is mostly correct.",
            "改进建议": "Add more evidence.",
        }
    )
    client = FakeClient([english_response, english_response, english_response])
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    with pytest.raises(JudgeEvaluationError, match="裁判输出未使用中文") as exc_info:
        agent.evaluate(make_item(), make_answer())

    assert len(client.requests) == 3
    assert API_KEY not in str(exc_info.value)


def test_judge_requires_both_reason_and_suggestion_to_contain_chinese() -> None:
    english_suggestion = valid_judge_response(**{"改进建议": "Add details."})
    client = FakeClient([english_suggestion])
    agent = EvaluationJudgeAgent(
        make_config(judge_retry_attempts=0), http_client=client
    )

    with pytest.raises(JudgeEvaluationError, match="裁判输出未使用中文"):
        agent.evaluate(make_item(), make_answer())


def test_repair_prompt_truncates_invalid_output_and_uses_safe_error_summary() -> None:
    invalid_output = "敏感模型输出" * 1_000 + API_KEY + "后续输出" * 1_000
    client = FakeClient(
        [response_with_content(invalid_output), valid_judge_response()]
    )
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    agent.evaluate(make_item(), make_answer())

    repair_prompt = client.requests[1]["json"]["messages"][-1]["content"]
    assert "上一次输出无法通过评测结构校验" in repair_prompt
    assert "已截断" in repair_prompt
    assert len(repair_prompt) < len(invalid_output)
    assert API_KEY not in repair_prompt


def test_http_status_error_is_not_converted_to_format_retry() -> None:
    request = httpx.Request("POST", "https://judge.example.com/v1/chat/completions")
    response = httpx.Response(503, request=request)
    status_error = httpx.HTTPStatusError(
        "Service Unavailable", request=request, response=response
    )
    client = FakeClient(
        [FakeResponse(valid_judge_payload(), status_error=status_error)]
    )
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        agent.evaluate(make_item(), make_answer())

    assert exc_info.value is status_error
    assert len(client.requests) == 1


def test_transport_error_is_not_converted_to_format_retry() -> None:
    request = httpx.Request("POST", "https://judge.example.com/v1/chat/completions")
    transport_error = httpx.ReadTimeout("裁判读取超时", request=request)
    client = FakeClient([transport_error])
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    with pytest.raises(httpx.ReadTimeout) as exc_info:
        agent.evaluate(make_item(), make_answer())

    assert exc_info.value is transport_error
    assert len(client.requests) == 1


def test_injected_client_is_not_closed_by_agent_context_manager() -> None:
    client = FakeClient([valid_judge_response()])

    with EvaluationJudgeAgent(make_config(), http_client=client) as agent:
        assert agent.evaluate(make_item(), make_answer()).correctness_score == 30
    agent.close()

    assert client.close_calls == 0


def test_owned_client_is_closed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient([])
    created_with: list[object] = []

    def fake_httpx_client(*, timeout: float) -> FakeClient:
        created_with.append(timeout)
        return client

    monkeypatch.setattr(httpx, "Client", fake_httpx_client)
    agent = EvaluationJudgeAgent(make_config())

    agent.close()
    agent.close()

    assert created_with == [23.5]
    assert client.close_calls == 1
