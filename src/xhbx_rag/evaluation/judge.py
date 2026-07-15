from __future__ import annotations

import json
import re
from types import TracebackType
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from xhbx_rag.evaluation.config import EvaluationConfig
from xhbx_rag.evaluation.models import ERROR_TAGS, EvaluationItem, JudgeResult


INVALID_OUTPUT_EXCERPT_CHARS = 2_000
ERROR_SUMMARY_CHARS = 600
FINAL_ERROR_CHARS = 800


class JudgeEvaluationError(RuntimeError):
    """裁判模型多次输出无效评测结果时抛出。"""


class _JudgeResponse(Protocol):
    def raise_for_status(self) -> None:
        """校验 HTTP 状态码。"""

    def json(self) -> object:
        """解析响应 JSON。"""


class _JudgeHttpClient(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: dict,
        json: dict,
        timeout: float,
    ) -> _JudgeResponse:
        """向裁判模型发送 JSON 请求。"""

    def close(self) -> None:
        """关闭 HTTP 客户端。"""


class _ChineseExplanationError(ValueError):
    """扣分原因或改进建议没有使用中文。"""


class EvaluationJudgeAgent:
    def __init__(
        self,
        config: EvaluationConfig,
        http_client: _JudgeHttpClient | None = None,
    ) -> None:
        self.config = config
        self.http_client = (
            http_client
            if http_client is not None
            else httpx.Client(timeout=config.judge_timeout)
        )
        self._owns_client = http_client is None
        self._closed = False

    def evaluate(
        self,
        item: EvaluationItem,
        answer_response: dict[str, Any],
    ) -> JudgeResult:
        base_messages = build_judge_messages(item, answer_response)
        messages = base_messages
        last_error: ValueError | RecursionError | None = None

        for _attempt in range(self.config.judge_retry_attempts + 1):
            response = self.http_client.post(
                self.config.judge_base_url.rstrip("/") + "/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.judge_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.judge_model_name,
                    "messages": messages,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
                timeout=self.config.judge_timeout,
            )
            response.raise_for_status()

            invalid_output = ""
            try:
                payload = response.json()
                invalid_output = _safe_payload_text(payload)
                content = _extract_content(payload)
                invalid_output = content
                result = JudgeResult.model_validate_json(
                    strip_json_fences(content)
                )
                require_chinese_explanation(
                    result.reason,
                    result.improvement_suggestion,
                )
                return result
            except (ValidationError, ValueError, RecursionError) as exc:
                last_error = exc
                messages = repair_messages(
                    base_messages,
                    invalid_output,
                    exc,
                    secret=self.config.judge_api_key,
                )

        final_message = safe_judge_error(
            last_error,
            secret=self.config.judge_api_key,
        )
        raise JudgeEvaluationError(final_message) from last_error

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            self.http_client.close()

    def __enter__(self) -> EvaluationJudgeAgent:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def build_judge_messages(
    item: EvaluationItem,
    answer_response: dict[str, Any],
) -> list[dict[str, str]]:
    answer = answer_response.get("answer", "")
    retrieval_evidences = answer_response.get("retrieval_evidences", [])
    citations = answer_response.get("citations", [])
    context = {
        "问题": item.question,
        "参考答案": item.reference_answer,
        "溯源状态": item.trace_status,
        "主chunk_id": item.primary_chunk_id,
        "黄金chunk_id列表": item.gold_chunk_ids,
        "黄金证据": [
            evidence.model_dump(mode="json", by_alias=True)
            for evidence in item.gold_evidences
        ],
        "智能体回答": answer,
        "检索证据": retrieval_evidences,
        "引用": citations,
    }
    user_prompt = (
        "请评测下面这一条问答。参考答案用于提取关键点和比较回答，"
        "但其中的未溯源扩写不应被视为自动真理；"
        "语义等价的不同措辞不应扣减事实正确性得分。\n"
        "评测上下文：\n"
        + json.dumps(context, ensure_ascii=False, indent=2, default=_json_default)
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def strip_json_fences(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return match.group(1) if match else stripped


def require_chinese_explanation(reason: str, suggestion: str) -> None:
    if not _contains_chinese(reason) or not _contains_chinese(suggestion):
        raise _ChineseExplanationError(
            "裁判输出未使用中文：扣分原因和改进建议"
            "必须各自至少包含一个汉字"
        )


def repair_messages(
    base_messages: list[dict[str, str]],
    invalid_output: str,
    error: ValueError | RecursionError,
    *,
    secret: str,
) -> list[dict[str, str]]:
    safe_output = _redact_secret(
        _bounded_text(
            invalid_output or "（未取得有效文本输出）",
            INVALID_OUTPUT_EXCERPT_CHARS,
        ),
        secret,
    )
    error_summary = _redact_secret(_safe_error_summary(error), secret)
    repair_prompt = (
        "上一次输出无法通过评测结构校验，"
        "请严格按系统提示重新输出完整的"
        "中文别名 JSON object，不要输出 Markdown 或解释文字。\n"
        f"安全错误摘要：{error_summary}\n"
        f"上一次输出（已限制长度）：{safe_output}"
    )
    return [*base_messages, {"role": "user", "content": repair_prompt}]


def safe_judge_error(
    error: ValueError | RecursionError | None,
    *,
    secret: str,
) -> str:
    if isinstance(error, _ChineseExplanationError):
        message = str(error)
    else:
        message = f"裁判输出结构校验失败：{_safe_error_summary(error)}"
    return _bounded_text(_redact_secret(message, secret), FINAL_ERROR_CHARS)


def _extract_content(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("裁判响应必须是 JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("裁判响应缺少 choices")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ValueError("裁判响应 choices[0] 结构无效")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("裁判响应缺少 message")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("裁判响应缺少字符串 message.content")
    return content


def _contains_chinese(text: str) -> bool:
    return re.search(r"[\u4e00-\u9fff]", text) is not None


def _safe_payload_text(payload: object) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, default=_json_default)
    except (TypeError, ValueError, RecursionError):
        return "（裁判响应无法安全序列化）"


def _json_default(value: object) -> str:
    return f"（无法序列化的 {type(value).__name__}）"


def _safe_error_summary(
    error: ValueError | RecursionError | None,
) -> str:
    if error is None:
        return "裁判输出未通过校验"
    if isinstance(error, _ChineseExplanationError):
        return str(error)
    if isinstance(error, json.JSONDecodeError):
        return _bounded_text(
            "裁判输出不是有效 JSON，"
            f"第 {error.lineno} 行第 {error.colno} 列解析失败",
            ERROR_SUMMARY_CHARS,
        )
    if isinstance(error, ValidationError):
        details: list[str] = []
        for item in error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        ):
            location = item.get("loc", ())
            if isinstance(location, (list, tuple)):
                safe_location = ".".join(str(part) for part in location)
            else:
                safe_location = str(location)
            details.append(f"字段 {safe_location or '根对象'} 无效")
        detail_text = "；".join(details) or "裁判 JSON 字段无效"
        return _bounded_text(detail_text, ERROR_SUMMARY_CHARS)
    if isinstance(error, RecursionError):
        return "裁判响应嵌套过深"
    return _bounded_text(str(error), ERROR_SUMMARY_CHARS)


def _bounded_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = "...（已截断）..."
    remaining = limit - len(marker)
    if remaining <= 0:
        return marker[:limit]
    head_chars = (remaining + 1) // 2
    tail_chars = remaining - head_chars
    tail = text[-tail_chars:] if tail_chars else ""
    return f"{text[:head_chars]}{marker}{tail}"


def _redact_secret(text: str, secret: str) -> str:
    if not secret:
        return text
    return text.replace(secret, "（已隐藏敏感信息）")


_SYSTEM_PROMPT = f"""你是独立的保险销售问答评测裁判。
评分依据仅限问题、参考答案、黄金证据、智能体回答和检索证据；溯源状态、
主chunk_id、黄金chunk_id列表和引用只用于理解证据关系。参考答案中未被溯源证据支持的扩写不是自动真理。
语义相同的回答即使措辞不同也应视为事实一致，不得因措辞不同扣减事实正确性得分。

五维评分口径固定如下：
1. 事实正确性得分：0 至 35 分，判断回答中的事实是否正确。
2. 关键点覆盖得分：0 至 20 分，判断参考答案中有依据的关键点是否覆盖。
3. 证据忠实性得分：0 至 20 分，判断回答是否忠于黄金证据和检索证据、有无无依据扩写。
4. 相关性与表达得分：0 至 10 分，判断是否切题、清晰、简洁、可执行。
5. 引用及黄金来源命中得分：0 至 15 分，由程序的确定性规则独立计算。
你只负责前四项共 85 分。不得生成、估算或输出第 5 项的 15 分，也不得输出总分或等级。

错误标签只能从以下固定中文枚举中选择，可以为空：{json.dumps(ERROR_TAGS, ensure_ascii=False)}。
扣分原因和改进建议必须分别使用简体中文，并各自至少包含一个汉字。
只输出一个符合 JudgeResult 中文别名的 JSON object，不得输出 Markdown 代码围栏或其他文字。
JSON object 必须且只能包含以下字段：
- 事实正确性得分：number
- 关键点覆盖得分：number
- 证据忠实性得分：number
- 相关性与表达得分：number
- 参考答案关键点：string 数组
- 已覆盖关键点：string 数组
- 缺失关键点：string 数组
- 无依据表述：string 数组
- 错误标签：固定中文枚举组成的 string 数组
- 扣分原因：简体中文 string
- 改进建议：简体中文 string
"""
