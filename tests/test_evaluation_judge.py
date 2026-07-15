from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

import xhbx_rag.evaluation.judge as judge_module
from xhbx_rag.evaluation.config import EvaluationConfig
from xhbx_rag.evaluation.judge import (
    EvaluationJudgeAgent,
    JudgeEvaluationError,
    build_judge_messages,
)
from xhbx_rag.evaluation.models import (
    ERROR_TAGS,
    EvaluationItem,
    GoldEvidence,
    JudgeResult,
)


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


def test_final_judge_error_has_no_sensitive_original_exception_chain() -> None:
    unbounded_output = API_KEY + "-原始无界模型输出" * 3_000
    invalid_contract = valid_judge_payload(
        **{
            "事实正确性得分": 36,
            "扣分原因": unbounded_output,
        }
    )
    client = FakeClient(
        [
            FakeResponse(
                json.JSONDecodeError("无效响应 JSON", unbounded_output, 0)
            ),
            response_with_content(
                json.dumps(invalid_contract, ensure_ascii=False)
            ),
            response_with_content(unbounded_output),
        ]
    )
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    with pytest.raises(JudgeEvaluationError) as exc_info:
        agent.evaluate(make_item(), make_answer())

    error = exc_info.value
    assert error.__cause__ is None
    assert error.__context__ is None
    judge_locals = _judge_traceback_locals(error)
    assert judge_locals
    assert all("self" not in values for values in judge_locals)
    exception_text = _deep_sensitive_text(
        {"异常": error, "裁判帧局部": judge_locals}
    )
    assert API_KEY not in exception_text
    assert unbounded_output not in exception_text


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


def test_judge_retries_valid_result_that_echoes_secret() -> None:
    unsafe_response = valid_judge_response(
        **{"扣分原因": f"回答基本正确，但回显了 {API_KEY}。"}
    )
    client = FakeClient([unsafe_response, valid_judge_response()])
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    result = agent.evaluate(make_item(), make_answer())

    assert len(client.requests) == 2
    assert "上一次输出无法通过评测结构校验" in (
        client.requests[1]["json"]["messages"][-1]["content"]
    )
    result_json = result.model_dump_json(by_alias=True)
    assert API_KEY not in result_json


def test_judge_rejects_sensitive_strings_in_result_lists_after_retries() -> None:
    unsafe_response = valid_judge_response(
        **{
            "参考答案关键点": [f"客户手机号 13812345678", API_KEY],
            "已覆盖关键点": ["邮箱 customer@example.com"],
            "缺失关键点": ["身份证 11010519491231002X"],
        }
    )
    client = FakeClient([unsafe_response, unsafe_response, unsafe_response])
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    with pytest.raises(
        JudgeEvaluationError,
        match="裁判输出包含敏感信息",
    ) as exc_info:
        agent.evaluate(make_item(), make_answer())

    assert len(client.requests) == 3
    error_text = _deep_sensitive_text(exc_info.value)
    assert API_KEY not in error_text
    assert "13812345678" not in error_text
    assert "customer@example.com" not in error_text
    assert "11010519491231002X" not in error_text


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


def test_repair_prompt_sanitizes_common_pii_and_phone_formats() -> None:
    unsafe_values = _unsafe_pii_values()
    unsafe_output = "；".join([API_KEY, *unsafe_values])
    client = FakeClient(
        [response_with_content(unsafe_output), valid_judge_response()]
    )
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    agent.evaluate(make_item(), make_answer())

    repair_prompt = client.requests[1]["json"]["messages"][-1]["content"]
    assert API_KEY not in repair_prompt
    assert all(value not in repair_prompt for value in unsafe_values)
    assert "+86" not in repair_prompt
    assert "0086" not in repair_prompt
    assert "邮箱已隐藏" in repair_prompt
    assert "手机号已隐藏" in repair_prompt
    assert "身份证号已隐藏" in repair_prompt


def test_safe_judge_error_sanitizes_common_pii_and_phone_formats() -> None:
    unsafe_values = _unsafe_pii_values()
    unsafe_message = "；".join([API_KEY, *unsafe_values])

    message = judge_module.safe_judge_error(
        ValueError(unsafe_message),
        secret=API_KEY,
    )

    assert API_KEY not in message
    assert all(value not in message for value in unsafe_values)
    assert "+86" not in message
    assert "0086" not in message
    assert "邮箱已隐藏" in message
    assert "手机号已隐藏" in message
    assert "身份证号已隐藏" in message


def test_build_judge_messages_projects_only_safe_scoring_context() -> None:
    item = _sensitive_large_item()
    answer_response = _sensitive_large_answer()

    messages = build_judge_messages(
        item,
        answer_response,
        secret=API_KEY,
    )

    user_prompt = messages[1]["content"]
    context = _user_context(messages)
    assert API_KEY not in user_prompt
    assert "customer@example.com" not in user_prompt
    assert "13812345678" not in user_prompt
    assert "11010519491231002X" not in user_prompt
    assert "邮箱已隐藏" in user_prompt
    assert "手机号已隐藏" in user_prompt
    assert "身份证号已隐藏" in user_prompt
    assert "METADATA-LEAK" not in user_prompt
    assert "INTERNAL-CITATION-LEAK" not in user_prompt
    assert "/Users/private/customer" not in user_prompt

    assert set(context) == {
        "问题",
        "参考答案",
        "溯源状态",
        "主chunk_id",
        "黄金chunk_id列表",
        "黄金证据",
        "智能体回答",
        "检索证据",
        "引用",
    }
    assert all(
        set(evidence) == {
            "chunk_id",
            "来源路径",
            "来源定位",
            "原文摘录",
            "支撑说明",
        }
        for evidence in context["黄金证据"]
        if "chunk_id" in evidence
    )
    assert context["黄金证据"][0]["来源路径"] == "gold-1.docx"
    assert all(
        set(evidence) == {"chunk_id", "chunk类型", "证据正文"}
        for evidence in context["检索证据"]
        if "chunk_id" in evidence
    )
    assert context["引用"][0]["来源路径"] == "citation-1.docx"
    assert set(context["引用"][0]) <= {
        "证据序号",
        "chunk_id",
        "来源路径",
        "来源定位",
        "原文摘录",
        "引用原文",
        "展示摘录",
    }
    locator = context["引用"][0]["来源定位"]
    assert "起始行" in locator
    assert "标题路径" in locator
    assert "container" not in locator


def test_evaluate_keeps_all_context_categories_within_explicit_budgets() -> None:
    client = FakeClient([valid_judge_response()])
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    agent.evaluate(_sensitive_large_item(), _sensitive_large_answer())

    messages = client.requests[0]["json"]["messages"]
    user_prompt = messages[1]["content"]
    context = _user_context(messages)
    assert len(user_prompt) <= judge_module.JUDGE_CONTEXT_CHAR_BUDGET
    assert len(context["问题"]) <= judge_module.QUESTION_CHAR_LIMIT
    assert (
        len(context["参考答案"])
        <= judge_module.REFERENCE_ANSWER_CHAR_LIMIT
    )
    assert len(context["智能体回答"]) <= judge_module.ANSWER_CHAR_LIMIT
    assert (
        _compact_json_chars(context["黄金证据"])
        <= judge_module.GOLD_EVIDENCE_CHAR_BUDGET
    )
    assert (
        _compact_json_chars(context["检索证据"])
        <= judge_module.RETRIEVAL_EVIDENCE_CHAR_BUDGET
    )
    assert (
        _compact_json_chars(context["引用"])
        <= judge_module.CITATION_CHAR_BUDGET
    )
    assert {row["chunk_id"] for row in context["黄金证据"]} >= {
        "gold-1",
        "gold-2",
        "gold-3",
    }
    assert {row["chunk_id"] for row in context["检索证据"]} >= {
        "retrieval-1",
        "retrieval-2",
        "retrieval-3",
    }
    assert {row["chunk_id"] for row in context["引用"]} >= {
        "retrieval-1",
        "retrieval-2",
        "retrieval-3",
    }
    assert "已截断" in user_prompt or "已省略" in user_prompt
    assert API_KEY not in json.dumps(
        client.requests[0]["json"], ensure_ascii=False
    )


def test_fit_json_value_guarantees_budget_for_nested_arrays_and_escapes() -> None:
    value = {
        "标题路径": [f"标题{index}\\\"" for index in range(10_000)],
        "正文": "\\\"复杂正文" * 10_000,
    }

    fitted = judge_module._fit_json_value(
        value,
        judge_module.CITATION_CHAR_BUDGET - 2,
    )

    assert (
        _compact_json_chars(fitted)
        <= judge_module.CITATION_CHAR_BUDGET - 2
    )
    assert "省略" in json.dumps(fitted, ensure_ascii=False)


@pytest.mark.parametrize("heading_count", [10_000, 30_000])
def test_single_oversized_citation_cannot_evict_other_context_categories(
    heading_count: int,
) -> None:
    escaped_text = "\\\"复杂内容" * 8_000
    item = make_item().model_copy(
        update={
            "question": escaped_text,
            "reference_answer": escaped_text,
            "gold_evidences": [
                GoldEvidence(
                    chunk_id="gold-real",
                    source_path="safe/gold.docx",
                    locator="安全定位",
                    excerpt=escaped_text,
                    support_note=escaped_text,
                )
            ],
        }
    )
    answer_response = {
        "answer": escaped_text,
        "retrieval_evidences": [
            {
                "chunk_id": "retrieval-real",
                "chunk_type": "script",
                "text": escaped_text,
            }
        ],
        "citations": [
            {
                "evidence_index": 1,
                "chunk_id": "retrieval-real",
                "source_path": "safe/citation.docx",
                "locator": {
                    "line_start": 1,
                    "heading_path": [
                        f"标题{index}\\\""
                        for index in range(heading_count)
                    ],
                },
                "quote": escaped_text,
            }
        ],
    }

    messages = build_judge_messages(item, answer_response, secret=API_KEY)

    user_prompt = messages[1]["content"]
    context = _user_context(messages)
    assert json.loads(user_prompt.split("评测上下文：\n", 1)[1]) == context
    assert len(user_prompt) <= judge_module.JUDGE_CONTEXT_CHAR_BUDGET
    assert (
        _compact_json_chars(context["问题"])
        <= judge_module.QUESTION_CHAR_LIMIT
    )
    assert (
        _compact_json_chars(context["参考答案"])
        <= judge_module.REFERENCE_ANSWER_CHAR_LIMIT
    )
    assert (
        _compact_json_chars(context["智能体回答"])
        <= judge_module.ANSWER_CHAR_LIMIT
    )
    assert (
        _compact_json_chars(context["黄金证据"])
        <= judge_module.GOLD_EVIDENCE_CHAR_BUDGET
    )
    assert (
        _compact_json_chars(context["检索证据"])
        <= judge_module.RETRIEVAL_EVIDENCE_CHAR_BUDGET
    )
    assert (
        _compact_json_chars(context["引用"])
        <= judge_module.CITATION_CHAR_BUDGET
    )
    assert context["黄金证据"][0]["chunk_id"] == "gold-real"
    assert context["检索证据"][0]["chunk_id"] == "retrieval-real"
    citation = context["引用"][0]
    assert citation["chunk_id"] == "retrieval-real"
    heading_path = citation["来源定位"]["标题路径"]
    assert len(heading_path) < heading_count
    assert any("省略" in value for value in heading_path)


def test_http_status_error_is_not_converted_to_format_retry() -> None:
    request = _sensitive_httpx_request()
    response_request = _sensitive_httpx_request()
    response = httpx.Response(503, request=response_request)
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
    _assert_sensitive_headers_redacted(status_error.request)
    _assert_sensitive_headers_redacted(status_error.response.request)
    assert API_KEY not in str(status_error)
    assert API_KEY not in repr(status_error)


def test_transport_error_is_not_converted_to_format_retry() -> None:
    request = _sensitive_httpx_request()
    transport_error = httpx.ReadTimeout("裁判读取超时", request=request)
    client = FakeClient([transport_error])
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    with pytest.raises(httpx.ReadTimeout) as exc_info:
        agent.evaluate(make_item(), make_answer())

    assert exc_info.value is transport_error
    assert len(client.requests) == 1
    _assert_sensitive_headers_redacted(transport_error.request)
    assert API_KEY not in str(transport_error)
    assert API_KEY not in repr(transport_error)


def test_transport_error_without_bound_request_is_preserved() -> None:
    transport_error = httpx.ReadTimeout("裁判连接尚未建立")
    client = FakeClient([transport_error])
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    with pytest.raises(httpx.ReadTimeout) as exc_info:
        agent.evaluate(make_item(), make_answer())

    assert exc_info.value is transport_error
    assert len(client.requests) == 1


@pytest.mark.parametrize("error_kind", ["transport", "status"])
def test_http_error_after_format_retry_clears_prior_sensitive_locals(
    error_kind: str,
) -> None:
    prior_output = API_KEY + "-前一轮原始模型输出" * 3_000
    request = _sensitive_httpx_request()
    if error_kind == "transport":
        expected_error: httpx.HTTPError = httpx.ReadTimeout(
            "裁判读取超时",
            request=request,
        )
        second_outcome: object = expected_error
    else:
        response_request = _sensitive_httpx_request()
        response = httpx.Response(503, request=response_request)
        expected_error = httpx.HTTPStatusError(
            "Service Unavailable",
            request=request,
            response=response,
        )
        second_outcome = FakeResponse({}, status_error=expected_error)
    client = FakeClient(
        [response_with_content(prior_output), second_outcome]
    )
    agent = EvaluationJudgeAgent(make_config(), http_client=client)

    with pytest.raises(type(expected_error)) as exc_info:
        agent.evaluate(make_item(), make_answer())

    error = exc_info.value
    assert error is expected_error
    judge_locals = _judge_traceback_locals(error)
    assert judge_locals
    assert all("self" not in values for values in judge_locals)
    sensitive_text = _deep_sensitive_text(
        {"异常": error, "裁判帧局部": judge_locals}
    )
    assert API_KEY not in sensitive_text
    assert prior_output not in sensitive_text


def test_success_return_clears_all_sensitive_evaluate_locals() -> None:
    client = FakeClient([valid_judge_response()])
    agent = EvaluationJudgeAgent(make_config(), http_client=client)
    item = make_item().model_copy(
        update={"question": f"成功路径敏感问题-{API_KEY}"}
    )
    answer_response = {
        **make_answer(),
        "answer": f"成功路径敏感回答-{API_KEY}",
    }
    return_locals: list[dict[str, object]] = []

    def capture_evaluate_return(frame: Any, event: str, arg: object) -> None:
        if (
            event == "return"
            and frame.f_code is EvaluationJudgeAgent.evaluate.__code__
        ):
            return_locals.append(dict(frame.f_locals))

    sys.setprofile(capture_evaluate_return)
    try:
        result = agent.evaluate(item, answer_response)
    finally:
        sys.setprofile(None)

    assert result.correctness_score == 30
    assert return_locals
    _assert_no_sensitive_runtime_state(
        return_locals,
        API_KEY,
        item.question,
        answer_response["answer"],
    )


def test_closed_owned_client_runtime_error_becomes_safe_judge_error() -> None:
    agent = EvaluationJudgeAgent(make_config(judge_retry_attempts=0))
    item = make_item().model_copy(
        update={"question": f"关闭客户端敏感问题-{API_KEY}"}
    )
    answer_response = {
        **make_answer(),
        "answer": f"关闭客户端敏感回答-{API_KEY}",
    }
    agent.close()

    with pytest.raises(
        JudgeEvaluationError,
        match="裁判执行失败，请稍后重试",
    ) as exc_info:
        agent.evaluate(item, answer_response)

    _assert_safe_unexpected_failure(
        exc_info.value,
        API_KEY,
        item.question,
        answer_response["answer"],
        "Cannot send a request",
    )


def test_build_runtime_error_becomes_safe_judge_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = make_item().model_copy(
        update={"question": f"构建阶段敏感问题-{API_KEY}"}
    )
    answer_response = {
        **make_answer(),
        "answer": f"构建阶段敏感回答-{API_KEY}",
    }
    monkeypatch.setattr(
        judge_module,
        "build_judge_messages",
        _raise_sensitive_build_error,
    )
    agent = EvaluationJudgeAgent(
        make_config(judge_retry_attempts=0),
        http_client=FakeClient([]),
    )

    with pytest.raises(
        JudgeEvaluationError,
        match="裁判执行失败，请稍后重试",
    ) as exc_info:
        agent.evaluate(item, answer_response)

    _assert_safe_unexpected_failure(
        exc_info.value,
        API_KEY,
        item.question,
        answer_response["answer"],
        "构建 helper 原始异常",
    )


def test_parser_runtime_error_becomes_safe_judge_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_output = f"解析阶段模型原文-{API_KEY}"
    monkeypatch.setattr(
        judge_module,
        "strip_json_fences",
        _raise_sensitive_parse_error,
    )
    client = FakeClient([response_with_content(sensitive_output)])
    agent = EvaluationJudgeAgent(
        make_config(judge_retry_attempts=0),
        http_client=client,
    )
    item = make_item().model_copy(
        update={"question": f"解析阶段敏感问题-{API_KEY}"}
    )
    answer_response = {
        **make_answer(),
        "answer": f"解析阶段敏感回答-{API_KEY}",
    }

    with pytest.raises(
        JudgeEvaluationError,
        match="裁判执行失败，请稍后重试",
    ) as exc_info:
        agent.evaluate(item, answer_response)

    _assert_safe_unexpected_failure(
        exc_info.value,
        API_KEY,
        item.question,
        answer_response["answer"],
        sensitive_output,
        "解析 helper 原始异常",
    )


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


def _judge_traceback_locals(error: BaseException) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        traceback = current.__traceback__
        while traceback is not None:
            frame = traceback.tb_frame
            if frame.f_globals.get("__name__") == "xhbx_rag.evaluation.judge":
                rows.append(dict(frame.f_locals))
            traceback = traceback.tb_next
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return rows


def _exception_runtime_locals(error: BaseException) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        traceback = current.__traceback__
        while traceback is not None:
            frame = traceback.tb_frame
            if not frame.f_code.co_name.startswith("test_"):
                rows.append(dict(frame.f_locals))
            traceback = traceback.tb_next
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return rows


def _assert_safe_unexpected_failure(
    error: JudgeEvaluationError,
    *sensitive_values: str,
) -> None:
    assert error.__cause__ is None
    assert error.__context__ is None
    runtime_locals = _exception_runtime_locals(error)
    assert runtime_locals
    _assert_no_sensitive_runtime_state(
        {"异常": error, "全部运行帧局部": runtime_locals},
        *sensitive_values,
    )


def _assert_no_sensitive_runtime_state(
    value: object,
    *sensitive_values: str,
) -> None:
    sensitive_text = _deep_sensitive_text(value)
    assert all(text not in sensitive_text for text in sensitive_values)
    assert not _deep_contains_instance(
        value,
        (EvaluationJudgeAgent, EvaluationConfig, JudgeResult),
    )


def _deep_contains_instance(
    value: object,
    expected_types: tuple[type[object], ...],
    seen: set[int] | None = None,
) -> bool:
    visited = seen if seen is not None else set()
    if isinstance(value, expected_types):
        return True
    if value is None or isinstance(value, (str, bytes, bool, int, float)):
        return False
    if id(value) in visited:
        return False
    visited.add(id(value))
    if isinstance(value, dict):
        return any(
            _deep_contains_instance(key, expected_types, visited)
            or _deep_contains_instance(item, expected_types, visited)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(
            _deep_contains_instance(item, expected_types, visited)
            for item in value
        )
    if isinstance(value, BaseException):
        return _deep_contains_instance(
            [vars(value), value.__cause__, value.__context__],
            expected_types,
            visited,
        )
    return False


def _deep_sensitive_text(value: object, seen: set[int] | None = None) -> str:
    visited = seen if seen is not None else set()
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return repr(value)
    if id(value) in visited:
        return ""
    visited.add(id(value))
    if isinstance(value, dict):
        return "\n".join(
            text
            for key, item in value.items()
            for text in (
                _deep_sensitive_text(key, visited),
                _deep_sensitive_text(item, visited),
            )
        )
    if isinstance(value, (list, tuple, set)):
        return "\n".join(
            _deep_sensitive_text(item, visited) for item in value
        )
    if isinstance(value, JudgeResult):
        return _deep_sensitive_text(value.model_dump(), visited)
    if isinstance(value, EvaluationConfig):
        return _deep_sensitive_text(vars(value), visited)
    if isinstance(value, EvaluationJudgeAgent):
        return _deep_sensitive_text(vars(value), visited)
    if isinstance(value, ValidationError):
        return "\n".join(
            (
                str(value),
                repr(value),
                _deep_sensitive_text(
                    value.errors(include_input=True, include_context=True),
                    visited,
                ),
            )
        )
    if isinstance(value, json.JSONDecodeError):
        return "\n".join((str(value), repr(value), value.doc))
    if isinstance(value, httpx.HTTPError):
        details: list[object] = [str(value), repr(value), vars(value)]
        try:
            details.append(dict(value.request.headers))
        except RuntimeError:
            pass
        response = getattr(value, "response", None)
        if response is not None:
            try:
                details.append(dict(response.request.headers))
            except RuntimeError:
                pass
        return _deep_sensitive_text(details, visited)
    if isinstance(value, BaseException):
        return _deep_sensitive_text(
            [
                str(value),
                repr(value),
                vars(value),
                value.__cause__,
                value.__context__,
            ],
            visited,
        )
    return repr(value)


def _sensitive_httpx_request() -> httpx.Request:
    return httpx.Request(
        "POST",
        "https://judge.example.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Cookie": f"session={API_KEY}",
            "X-Api-Key": API_KEY,
        },
    )


def _assert_sensitive_headers_redacted(request: httpx.Request) -> None:
    assert request.headers["Authorization"] == "[REDACTED]"
    assert request.headers["Cookie"] == "[REDACTED]"
    assert request.headers["X-Api-Key"] == "[REDACTED]"
    assert API_KEY not in repr(request.headers)


def _sensitive_large_item() -> EvaluationItem:
    pii = (
        f"{API_KEY} 联系人customer@example.com，电话13812345678，"
        "证件11010519491231002X"
    )
    return EvaluationItem(
        item_id="row-sensitive",
        excel_row=2,
        question=pii + " 超长问题" * 2_000,
        reference_answer=pii + " 超长参考答案" * 2_000,
        trace_status="完整支持",
        primary_chunk_id=f"primary-{API_KEY}",
        gold_chunk_ids=[f"gold-{index}-{pii}" for index in range(1, 4)],
        gold_evidences=[
            GoldEvidence(
                chunk_id=f"gold-{index}",
                source_path=f"/Users/private/customer/gold-{index}.docx",
                locator=pii + " 第三段" * 1_000,
                excerpt=pii + f" 黄金证据{index}" * 3_000,
                support_note=pii + f" 支撑说明{index}" * 2_000,
            )
            for index in range(1, 4)
        ],
    )


def _sensitive_large_answer() -> dict[str, object]:
    pii = (
        f"{API_KEY} 联系人customer@example.com，电话13812345678，"
        "证件11010519491231002X"
    )
    return {
        "answer": pii + " 超长智能体回答" * 3_000,
        "metadata": {"secret": f"METADATA-LEAK-{pii}"},
        "retrieval_evidences": [
            {
                "chunk_id": f"retrieval-{index}",
                "chunk_type": "script",
                "text": pii + f" 检索证据{index}" * 5_000,
                "score": 0.99,
                "rerank_score": 0.98,
                "tag_boost_factor": 2,
                "metadata": {"secret": f"METADATA-LEAK-{pii}"},
                "citations": [
                    {"quote": f"INTERNAL-CITATION-LEAK-{pii}"}
                ],
            }
            for index in range(1, 4)
        ],
        "citations": [
            {
                "evidence_index": index,
                "chunk_id": f"retrieval-{index}",
                "source_path": (
                    f"/Users/private/customer/citation-{index}.docx"
                ),
                "locator": {
                    "line_start": index,
                    "line_end": index + 1,
                    "heading_path": [pii, f"标题{index}"],
                    "container": f"/private/{API_KEY}/slide.xml",
                    "internal": f"METADATA-LEAK-{pii}",
                },
                "source_excerpt": pii + f" 原文摘录{index}" * 2_000,
                "quote": pii + f" 引用原文{index}" * 2_000,
                "display_excerpt": pii + f" 展示摘录{index}" * 2_000,
                "metadata": {"secret": f"METADATA-LEAK-{pii}"},
            }
            for index in range(1, 4)
        ],
    }


def _user_context(messages: list[dict[str, str]]) -> dict[str, object]:
    user_prompt = messages[1]["content"]
    return json.loads(user_prompt.split("评测上下文：\n", 1)[1])


def _compact_json_chars(value: object) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def _unsafe_pii_values() -> list[str]:
    return [
        "联系人customer@example.com",
        "手机13812345678",
        "国际+8613812345678",
        "国际+86 138 1234 5678",
        "国际8613812345678",
        "国际0086-13812345678",
        "分隔138-1234-5678",
        "证件11010519491231002X",
    ]


def _raise_sensitive_build_error(
    item: EvaluationItem,
    answer_response: dict[str, Any],
    *,
    secret: str,
) -> list[dict[str, str]]:
    raise RuntimeError(
        "构建 helper 原始异常："
        f"{secret}；{item.question}；{answer_response['answer']}"
    )


def _raise_sensitive_parse_error(content: str) -> str:
    raise RuntimeError(f"解析 helper 原始异常：{content}")
