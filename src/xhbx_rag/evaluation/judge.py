from __future__ import annotations

import json
import re
from collections.abc import Callable
from types import TracebackType
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from xhbx_rag.evaluation.config import EvaluationConfig
from xhbx_rag.evaluation.models import ERROR_TAGS, EvaluationItem, JudgeResult


INVALID_OUTPUT_EXCERPT_CHARS = 2_000
ERROR_SUMMARY_CHARS = 600
FINAL_ERROR_CHARS = 800
UNEXPECTED_JUDGE_ERROR_MESSAGE = "裁判执行失败，请稍后重试"
QUESTION_CHAR_LIMIT = 2_000
REFERENCE_ANSWER_CHAR_LIMIT = 3_000
ANSWER_CHAR_LIMIT = 4_000
TECHNICAL_ID_CHAR_LIMIT = 300
SOURCE_PATH_CHAR_LIMIT = 512
GOLD_CHUNK_IDS_CHAR_BUDGET = 1_000
GOLD_EVIDENCE_CHAR_BUDGET = 10_000
RETRIEVAL_EVIDENCE_CHAR_BUDGET = 20_000
CITATION_CHAR_BUDGET = 6_000
JUDGE_CONTEXT_CHAR_BUDGET = 50_000
MIN_PROJECTED_ITEM_CHARS = 256
NESTED_LIST_ITEM_LIMITS = (4_096, 1_024, 256, 64, 16, 4, 1, 0)
NESTED_STRING_CHAR_LIMITS = (4_096, 2_048, 1_024, 512, 256, 128, 64, 32, 16)
SECRET_PLACEHOLDER = "【API密钥已隐藏】"
EMAIL_PLACEHOLDER = "【邮箱已隐藏】"
PHONE_PLACEHOLDER = "【手机号已隐藏】"
IDENTITY_PLACEHOLDER = "【身份证号已隐藏】"
SENSITIVE_HTTP_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
        "x-auth-token",
        "x-access-token",
    }
)
REDACTED_HEADER_VALUE = "[REDACTED]"


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


class _UnsafeJudgeOutputError(ValueError):
    """裁判结果包含不允许返回的敏感字符串。"""


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
        http_client: _JudgeHttpClient | None = None
        endpoint = ""
        model_name = ""
        timeout = 0.0
        retry_attempts = 0
        api_key = ""
        base_messages: list[dict[str, str]] = []
        messages: list[dict[str, str]] = []
        last_error_message = ""
        final_error_message = UNEXPECTED_JUDGE_ERROR_MESSAGE
        response: _JudgeResponse | None = None
        payload: object = None
        content = ""
        invalid_output = ""
        result: JudgeResult | None = None
        try:
            http_client = self.http_client
            endpoint = (
                self.config.judge_base_url.rstrip("/") + "/chat/completions"
            )
            model_name = self.config.judge_model_name
            timeout = self.config.judge_timeout
            retry_attempts = self.config.judge_retry_attempts
            api_key = self.config.judge_api_key
            base_messages = build_judge_messages(
                item,
                answer_response,
                secret=api_key,
            )
            messages = base_messages
            last_error_message = "裁判输出未通过校验"

            for _attempt in range(retry_attempts + 1):
                response = None
                payload = None
                content = ""
                invalid_output = ""
                result = None
                try:
                    response = http_client.post(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model_name,
                            "messages": messages,
                            "temperature": 0,
                            "response_format": {"type": "json_object"},
                        },
                        timeout=timeout,
                    )
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    _redact_http_error_headers(exc)
                    raise

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
                    require_safe_judge_result(result, api_key)
                    return result
                except (ValidationError, ValueError, RecursionError) as exc:
                    last_error_message = safe_judge_error(
                        exc,
                        secret=api_key,
                    )
                    messages = repair_messages(
                        base_messages,
                        invalid_output,
                        exc,
                        secret=api_key,
                    )

            final_error_message = last_error_message
        except httpx.HTTPError:
            raise
        except Exception:
            final_error_message = UNEXPECTED_JUDGE_ERROR_MESSAGE
        finally:
            item = None  # type: ignore[assignment]
            answer_response = {}
            api_key = ""
            http_client = None
            base_messages = []
            messages = []
            response = None
            payload = None
            content = ""
            invalid_output = ""
            result = None
            endpoint = ""
            model_name = ""
            timeout = 0.0
            retry_attempts = 0
            last_error_message = ""
            del self

        raise JudgeEvaluationError(final_error_message) from None

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
    *,
    secret: str = "",
) -> list[dict[str, str]]:
    answer = _safe_input_text(answer_response.get("answer", ""), secret)
    raw_retrieval_evidences = answer_response.get("retrieval_evidences", [])
    raw_citations = answer_response.get("citations", [])
    context = {
        "问题": _fit_json_string(
            _sanitize_prompt_text(item.question, secret),
            QUESTION_CHAR_LIMIT,
        ),
        "参考答案": _fit_json_string(
            _sanitize_prompt_text(item.reference_answer, secret),
            REFERENCE_ANSWER_CHAR_LIMIT,
        ),
        "溯源状态": item.trace_status,
        "主chunk_id": _bounded_text(
            _sanitize_prompt_text(item.primary_chunk_id, secret),
            TECHNICAL_ID_CHAR_LIMIT,
        ),
        "黄金chunk_id列表": _project_string_list(
            item.gold_chunk_ids,
            secret=secret,
            budget=GOLD_CHUNK_IDS_CHAR_BUDGET,
        ),
        "黄金证据": _project_collection(
            item.gold_evidences,
            secret=secret,
            budget=GOLD_EVIDENCE_CHAR_BUDGET,
            projector=_project_gold_evidence,
        ),
        "智能体回答": _fit_json_string(answer, ANSWER_CHAR_LIMIT),
        "检索证据": _project_collection(
            _dict_rows(raw_retrieval_evidences),
            secret=secret,
            budget=RETRIEVAL_EVIDENCE_CHAR_BUDGET,
            projector=_project_retrieval_evidence,
        ),
        "引用": _project_collection(
            _dict_rows(raw_citations),
            secret=secret,
            budget=CITATION_CHAR_BUDGET,
            projector=_project_citation,
        ),
    }
    prompt_prefix = (
        "请评测下面这一条问答。参考答案用于提取关键点和比较回答，"
        "但其中的未溯源扩写不应被视为自动真理；"
        "语义等价的不同措辞不应扣减事实正确性得分。\n"
        "评测上下文：\n"
    )
    context_json = _fit_context_json(
        context,
        JUDGE_CONTEXT_CHAR_BUDGET - len(prompt_prefix),
    )
    user_prompt = prompt_prefix + context_json
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def _project_gold_evidence(
    evidence: object,
    secret: str,
    item_budget: int,
) -> dict[str, object]:
    projected = {
        "chunk_id": _sanitize_prompt_text(evidence.chunk_id, secret),
        "来源路径": _safe_source_path(evidence.source_path, secret),
        "来源定位": _sanitize_prompt_text(evidence.locator, secret),
        "原文摘录": _sanitize_prompt_text(evidence.excerpt, secret),
        "支撑说明": _sanitize_prompt_text(evidence.support_note, secret),
    }
    return _fit_json_value(projected, item_budget)


def _project_retrieval_evidence(
    evidence: object,
    secret: str,
    item_budget: int,
) -> dict[str, object]:
    row = evidence if isinstance(evidence, dict) else {}
    projected = {
        "chunk_id": _safe_input_text(row.get("chunk_id", ""), secret),
        "chunk类型": _safe_input_text(row.get("chunk_type", ""), secret),
        "证据正文": _safe_input_text(row.get("text", ""), secret),
    }
    return _fit_json_value(projected, item_budget)


def _project_citation(
    citation: object,
    secret: str,
    item_budget: int,
) -> dict[str, object]:
    row = citation if isinstance(citation, dict) else {}
    projected: dict[str, object] = {}
    if "evidence_index" in row:
        projected["证据序号"] = _safe_scalar(
            row.get("evidence_index"),
            secret,
        )
    if "chunk_id" in row:
        projected["chunk_id"] = _safe_input_text(
            row.get("chunk_id", ""),
            secret,
        )
    if "source_path" in row:
        projected["来源路径"] = _safe_source_path(
            row.get("source_path"),
            secret,
        )
    if "locator" in row:
        projected["来源定位"] = _project_locator(row.get("locator"), secret)
    for source_key, output_key in (
        ("source_excerpt", "原文摘录"),
        ("quote", "引用原文"),
        ("display_excerpt", "展示摘录"),
    ):
        if source_key in row:
            projected[output_key] = _safe_input_text(
                row.get(source_key, ""),
                secret,
            )
    return _fit_json_value(projected, item_budget)


def _project_locator(value: object, secret: str) -> dict[str, object]:
    if isinstance(value, str):
        return {"定位说明": _sanitize_prompt_text(value, secret)}
    if not isinstance(value, dict):
        return {}
    mappings = (
        ("line_start", "起始行"),
        ("line_end", "结束行"),
        ("char_start", "起始字符"),
        ("char_end", "结束字符"),
        ("heading_path", "标题路径"),
        ("page", "页码"),
        ("page_start", "起始页"),
        ("page_end", "结束页"),
        ("slide", "幻灯片页码"),
        ("slide_start", "起始幻灯片"),
        ("slide_end", "结束幻灯片"),
        ("heading", "标题"),
    )
    projected: dict[str, object] = {}
    for source_key, output_key in mappings:
        if source_key not in value:
            continue
        raw = value[source_key]
        if isinstance(raw, list):
            projected[output_key] = [
                _safe_input_text(item, secret)
                for item in raw
                if isinstance(item, (str, int, float))
                and not isinstance(item, bool)
            ]
        else:
            projected[output_key] = _safe_scalar(raw, secret)
    return projected


def _project_collection(
    rows: list[object],
    *,
    secret: str,
    budget: int,
    projector: Callable[[object, str, int], dict[str, object]],
) -> list[dict[str, object]]:
    if not rows:
        return []
    max_items = max(1, (budget - 256) // MIN_PROJECTED_ITEM_CHARS)
    selected = rows[:max_items]
    omitted = len(rows) - len(selected)
    marker = (
        {"省略说明": f"另有 {omitted} 项因上下文预算已省略"}
        if omitted
        else None
    )
    marker_chars = _compact_json_chars(marker) if marker is not None else 0
    separators = max(0, len(selected) - 1) + (1 if marker else 0)
    available = budget - marker_chars - separators - 2
    item_budget = max(
        MIN_PROJECTED_ITEM_CHARS,
        available // len(selected),
    )
    projected = [
        projector(row, secret, item_budget)
        for row in selected
    ]
    if marker is not None:
        projected.append(marker)
    while _compact_json_chars(projected) > budget and len(projected) > 1:
        projected.pop(-2 if marker is not None else -1)
        omitted += 1
        marker = {"省略说明": f"另有 {omitted} 项因上下文预算已省略"}
        if projected and "省略说明" in projected[-1]:
            projected[-1] = marker
        else:
            projected.append(marker)
    if _compact_json_chars(projected) > budget:
        projected = [{"省略说明": "该类别单项超过上下文预算，已省略"}]
    return projected


def _project_string_list(
    values: list[str],
    *,
    secret: str,
    budget: int,
) -> list[str]:
    sanitized = [_sanitize_prompt_text(value, secret) for value in values]
    if not sanitized:
        return []
    max_items = max(1, budget // 64)
    selected = sanitized[:max_items]
    omitted = len(sanitized) - len(selected)
    suffix = f"...（另有 {omitted} 项已省略）..." if omitted else None
    suffix_chars = _compact_json_chars(suffix) + 1 if suffix else 0
    available = budget - suffix_chars - 2 - max(0, len(selected) - 1)
    item_budget = max(18, available // len(selected))
    projected = [_fit_json_string(value, item_budget) for value in selected]
    if suffix:
        projected.append(suffix)
    while _compact_json_chars(projected) > budget and len(projected) > 1:
        projected.pop(-2 if suffix else -1)
        omitted += 1
        suffix = f"...（另有 {omitted} 项已省略）..."
        if projected and "项已省略" in projected[-1]:
            projected[-1] = suffix
        else:
            projected.append(suffix)
    if _compact_json_chars(projected) > budget:
        projected = ["...（列表内容超过上下文预算，已省略）..."]
    return projected


def _fit_json_value(
    value: dict[str, object],
    budget: int,
) -> dict[str, object]:
    if _compact_json_chars(value) <= budget:
        return value
    for list_limit in NESTED_LIST_ITEM_LIMITS:
        for string_limit in NESTED_STRING_CHAR_LIMITS:
            candidate = _limit_nested_value(
                value,
                string_limit=string_limit,
                list_limit=list_limit,
            )
            if _compact_json_chars(candidate) <= budget:
                return candidate
    minimal = _minimalize_nested_value(value)
    if isinstance(minimal, dict) and _compact_json_chars(minimal) <= budget:
        return minimal
    fallback = {"省略说明": "该项内容超过上下文预算，已省略"}
    return fallback if _compact_json_chars(fallback) <= budget else {}


def _fit_context_json(context: dict[str, object], budget: int) -> str:
    encoded = _compact_json(context)
    if len(encoded) <= budget:
        return encoded
    candidate = dict(context)
    for divisor in (2, 4, 8, 16):
        candidate["问题"] = _fit_json_string(
            str(context["问题"]),
            max(64, QUESTION_CHAR_LIMIT // divisor),
        )
        candidate["参考答案"] = _fit_json_string(
            str(context["参考答案"]),
            max(64, REFERENCE_ANSWER_CHAR_LIMIT // divisor),
        )
        candidate["智能体回答"] = _fit_json_string(
            str(context["智能体回答"]),
            max(64, ANSWER_CHAR_LIMIT // divisor),
        )
        encoded = _compact_json(candidate)
        if len(encoded) <= budget:
            return encoded
    for key in ("引用", "黄金证据", "检索证据"):
        wrapped = _fit_json_value(
            {key: candidate[key]},
            max(256, _compact_json_chars(candidate[key]) // 2),
        )
        candidate[key] = wrapped.get(
            key,
            [{"省略说明": "该类别内容因整体上下文预算已省略"}],
        )
        encoded = _compact_json(candidate)
        if len(encoded) <= budget:
            return encoded
    minimal = dict(candidate)
    for key in ("引用", "黄金证据", "检索证据"):
        minimal[key] = [{"省略说明": "该类别内容因整体上下文预算已省略"}]
    minimal["黄金chunk_id列表"] = ["...（列表已省略）..."]
    minimal["问题"] = "...（问题已省略）..."
    minimal["参考答案"] = "...（参考答案已省略）..."
    minimal["智能体回答"] = "...（回答已省略）..."
    return _compact_json(minimal)


def _fit_json_string(text: str, budget: int) -> str:
    if _compact_json_chars(text) <= budget:
        return text
    limit = min(len(text), max(16, budget))
    while limit > 16:
        candidate = _bounded_text(text, limit)
        if _compact_json_chars(candidate) <= budget:
            return candidate
        limit = max(16, int(limit * 0.7))
    marker = "...（内容超过 JSON 预算，已省略）..."
    return marker if _compact_json_chars(marker) <= budget else ""


def _limit_nested_value(
    value: Any,
    *,
    string_limit: int,
    list_limit: int,
) -> Any:
    if isinstance(value, str):
        return _bounded_text(value, string_limit)
    if isinstance(value, list):
        selected = [
            _limit_nested_value(
                item,
                string_limit=string_limit,
                list_limit=list_limit,
            )
            for item in value[:list_limit]
        ]
        omitted = len(value) - len(selected)
        if omitted:
            selected.append(f"...（另有 {omitted} 项已省略）...")
        return selected
    if isinstance(value, dict):
        return {
            key: _limit_nested_value(
                item,
                string_limit=string_limit,
                list_limit=list_limit,
            )
            for key, item in value.items()
        }
    return value


def _minimalize_nested_value(value: Any) -> Any:
    if isinstance(value, str):
        return _fit_json_string(value, 48)
    if isinstance(value, list):
        return ["...（列表内容已省略）..."] if value else []
    if isinstance(value, dict):
        return {
            key: _minimalize_nested_value(item)
            for key, item in value.items()
        }
    return value


def _safe_source_path(value: object, secret: str) -> str:
    raw = _safe_input_text(value, secret)
    normalized = raw.replace("\\", "/").strip()
    if "://" in normalized:
        normalized = urlsplit(normalized).path
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    is_absolute = normalized.startswith("/") or bool(
        re.match(r"^[A-Za-z]:/", normalized)
    )
    if is_absolute or ".." in parts:
        display = parts[-1] if parts else "（来源文件已隐藏）"
    else:
        display = "/".join(parts)
    return _bounded_text(
        _sanitize_prompt_text(display, secret),
        SOURCE_PATH_CHAR_LIMIT,
    )


def _safe_scalar(value: object, secret: str) -> int | float | str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        return value
    return _safe_input_text(value, secret)


def _safe_input_text(value: object, secret: str) -> str:
    if isinstance(value, str):
        return _sanitize_prompt_text(value, secret)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _sanitize_prompt_text(text: str, secret: str) -> str:
    sanitized = text.replace(secret, SECRET_PLACEHOLDER) if secret else text
    sanitized = re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        EMAIL_PLACEHOLDER,
        sanitized,
    )
    sanitized = re.sub(
        r"(?<!\d)(?:(?:\+86|0086|86)[\s-]*)?"
        r"1[3-9](?:[\s-]*\d){9}(?!\d)",
        PHONE_PLACEHOLDER,
        sanitized,
    )
    return re.sub(
        r"(?<!\d)\d{17}[\dXx](?!\d)",
        IDENTITY_PLACEHOLDER,
        sanitized,
    )


def _dict_rows(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _compact_json_chars(value: object) -> int:
    return len(_compact_json(value))


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


def require_safe_judge_result(result: JudgeResult, secret: str) -> None:
    for text in _iter_string_values(result.model_dump(mode="python")):
        if _sanitize_prompt_text(text, secret) != text:
            raise _UnsafeJudgeOutputError(
                "裁判输出包含敏感信息，已拒绝返回"
            )


def _iter_string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _iter_string_values(item)]
    if isinstance(value, dict):
        return [
            text
            for item in value.values()
            for text in _iter_string_values(item)
        ]
    return []


def repair_messages(
    base_messages: list[dict[str, str]],
    invalid_output: str,
    error: ValueError | RecursionError,
    *,
    secret: str,
) -> list[dict[str, str]]:
    safe_output = _bounded_text(
        _sanitize_prompt_text(
            invalid_output or "（未取得有效文本输出）",
            secret,
        ),
        INVALID_OUTPUT_EXCERPT_CHARS,
    )
    error_summary = _sanitize_prompt_text(_safe_error_summary(error), secret)
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
    return _bounded_text(
        _sanitize_prompt_text(message, secret),
        FINAL_ERROR_CHARS,
    )


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


def _redact_http_error_headers(error: httpx.HTTPError) -> None:
    _redact_request_headers(_bound_request(error))
    response = getattr(error, "response", None)
    _redact_request_headers(_bound_request(response))


def _bound_request(value: object) -> httpx.Request | None:
    if value is None:
        return None
    try:
        request = value.request  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError):
        return None
    return request if isinstance(request, httpx.Request) else None


def _redact_request_headers(request: httpx.Request | None) -> None:
    if request is None:
        return
    for name in list(request.headers.keys()):
        if name.lower() in SENSITIVE_HTTP_HEADERS:
            request.headers[name] = REDACTED_HEADER_VALUE


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
