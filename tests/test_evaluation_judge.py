from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import httpx
import pytest

import xhbx_rag.evaluation.judge as judge_module
from xhbx_rag.evaluation.config import EvaluationConfig
from xhbx_rag.evaluation.judge import (
    EvaluationJudgeAgent,
    JudgeEvaluationError,
    build_judge_messages,
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
    exception_text = _exception_graph_and_judge_locals(error)
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


def _exception_graph_and_judge_locals(error: BaseException) -> str:
    parts: list[str] = []
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        parts.extend((str(current), repr(current)))
        traceback = current.__traceback__
        while traceback is not None:
            frame = traceback.tb_frame
            if frame.f_globals.get("__name__") == "xhbx_rag.evaluation.judge":
                parts.extend(
                    f"{name}={value!r}"
                    for name, value in frame.f_locals.items()
                )
            traceback = traceback.tb_next
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return "\n".join(parts)


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
