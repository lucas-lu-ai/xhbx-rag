from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .http_retry import (
    RETRYABLE_TRANSPORT_ERRORS,
    is_retryable_status_code,
    sleep_before_retry,
)
from .observability import TraceSink, emit_trace
from .query_understanding import QueryUnderstanding
from .search import search_evidence


logger = logging.getLogger(__name__)


ANSWER_STREAM_RETRYABLE_TRANSPORT_ERRORS = (
    *RETRYABLE_TRANSPORT_ERRORS,
    httpx.ReadError,
)


class AnswerGenerationError(RuntimeError):
    """Raised when answer generation cannot produce a valid grounded answer."""


MODEL_OUTPUT_ATTEMPTS = 3
INVALID_OUTPUT_EXCERPT_CHARS = 4_000
ERROR_SUMMARY_CHARS = 800


class IncompleteModelOutputError(AnswerGenerationError):
    """Raised after all model output correction attempts are exhausted."""

    def __init__(self, last_error: str) -> None:
        self.last_error = last_error
        super().__init__(
            f"模型输出不完整，已尝试 {MODEL_OUTPUT_ATTEMPTS} 次: {last_error}"
        )


class _SseHttpClient(Protocol):
    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: dict,
        json: dict,
        timeout: float,
    ) -> Any:
        """打开流式 HTTP 请求，返回可迭代 SSE 行的响应上下文管理器。"""


class _QueryAgent(Protocol):
    def understand(self, query: str) -> QueryUnderstanding:
        """Understand and rewrite raw query."""


class _EmbeddingClient(Protocol):
    def embed_query(self, text: str) -> list[float]:
        """Embed rewritten query."""


class _Store(Protocol):
    def search(self, vector: list[float], top_k: int, filters: dict) -> list[object]:
        """Search vector store."""


class _Reranker(Protocol):
    def rerank(self, query: str, documents: list[str], top_k: int) -> list[object]:
        """Rerank candidate documents."""


class _AnswerAgent(Protocol):
    def generate(self, search_result: dict[str, Any]) -> object:
        """Generate a grounded answer from a search result."""


class GeneratedAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answer: str
    citation_indexes: list[int] = Field(default_factory=list)
    # 思考模型的推理过程，由 AnswerAgent 从流式响应收集后回填，不来自模型 JSON 输出。
    reasoning: str = ""

    @field_validator("citation_indexes", mode="before")
    @classmethod
    def _citation_indexes(cls, value: object) -> list[int]:
        if value is None:
            return []
        if not isinstance(value, list):
            return []
        indexes: list[int] = []
        for item in value:
            try:
                index = int(item)
            except (TypeError, ValueError, OverflowError):
                continue
            if index > 0:
                indexes.append(index)
        return indexes

    @model_validator(mode="after")
    def _require_answer(self) -> "GeneratedAnswer":
        if not self.answer.strip():
            raise ValueError("answer 不能为空")
        return self


class _StreamStatusError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"chat/completions 流式响应状态码异常: {status_code}")
        self.status_code = status_code


@dataclass(frozen=True)
class _StreamChatResult:
    content: str
    saw_done: bool
    finish_reason: str | None = None
    stream_error: str | None = None


class AnswerAgent:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        http_client: _SseHttpClient | None = None,
        timeout: float = 60.0,
        retry_attempts: int = 3,
        retry_base_delay: float = 0.5,
        enable_thinking: bool = True,
        on_thinking_delta: Callable[[str], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.http_client = http_client or httpx.Client()
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_base_delay = retry_base_delay
        self.enable_thinking = enable_thinking
        self.on_thinking_delta = on_thinking_delta

    def generate(self, search_result: dict[str, Any]) -> GeneratedAnswer:
        base_messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(search_result)},
        ]
        messages = base_messages
        thinking_parts: list[str] = []
        last_error: str

        for attempt in range(1, MODEL_OUTPUT_ATTEMPTS + 1):
            body = {
                "model": self.model,
                "messages": messages,
                "temperature": 0,
                "stream": True,
                "enable_thinking": self.enable_thinking,
                "response_format": {"type": "json_object"},
            }
            attempt_thinking_parts: list[str] = []
            stream_result = self._stream_chat_content(body, attempt_thinking_parts)
            thinking_parts.extend(attempt_thinking_parts)
            content = stream_result.content
            model_output_error: ValueError | RecursionError | None = None
            try:
                _require_complete_stream(stream_result)
            except ValueError as exc:
                model_output_error = exc
            else:
                try:
                    data = json.loads(_strip_json_fences(content))
                except (json.JSONDecodeError, RecursionError) as exc:
                    model_output_error = exc
                else:
                    try:
                        generated = GeneratedAnswer.model_validate(data)
                    except ValidationError as exc:
                        model_output_error = exc

            if model_output_error is not None:
                last_error = _safe_model_error_summary(model_output_error)
                if attempt == MODEL_OUTPUT_ATTEMPTS:
                    break
                retry_notice = (
                    f"\n\n[第 {attempt} 次模型输出不完整，正在重新生成。]\n\n"
                )
                thinking_parts.append(retry_notice)
                if self.on_thinking_delta is not None:
                    self.on_thinking_delta(retry_notice)
                logger.warning(
                    "answer correction retry attempt=%d error_type=%s "
                    "content_chars=%d saw_done=%s finish_reason=%s",
                    attempt,
                    type(model_output_error).__name__,
                    len(content),
                    stream_result.saw_done,
                    _safe_finish_reason_for_log(stream_result.finish_reason),
                )
                messages = _retry_messages(
                    base_messages,
                    invalid_content=content,
                    error=model_output_error,
                )
                continue
            return generated.model_copy(update={"reasoning": "".join(thinking_parts)})

        safe_cause = AnswerGenerationError(last_error)
        raise IncompleteModelOutputError(last_error) from safe_cause

    def _stream_chat_content(
        self,
        body: dict[str, Any],
        thinking_parts: list[str],
    ) -> _StreamChatResult:
        attempts = max(1, self.retry_attempts)
        for attempt in range(1, attempts + 1):
            content_parts: list[str] = []
            received_delta = False
            saw_done = False
            finish_reason: str | None = None
            try:
                with self.http_client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=self.timeout,
                ) as response:
                    status_code = int(getattr(response, "status_code", 200))
                    if status_code >= 400:
                        raise _StreamStatusError(status_code)
                    for raw_line in response.iter_lines():
                        data = _sse_data(raw_line)
                        if data is None:
                            continue
                        if data == "[DONE]":
                            saw_done = True
                            break
                        try:
                            delta, chunk_finish_reason = _chunk_event(data)
                        except RecursionError:
                            return _StreamChatResult(
                                content="".join(content_parts),
                                saw_done=False,
                                finish_reason=finish_reason,
                                stream_error=(
                                    "SSE 数据块解析失败: JSON 嵌套过深"
                                ),
                            )
                        except ValueError as exc:
                            return _StreamChatResult(
                                content="".join(content_parts),
                                saw_done=False,
                                finish_reason=finish_reason,
                                stream_error=f"SSE 数据块解析失败: {exc}",
                            )
                        if chunk_finish_reason is not None:
                            finish_reason = chunk_finish_reason
                        reasoning = delta.get("reasoning_content")
                        if isinstance(reasoning, str) and reasoning:
                            received_delta = True
                            thinking_parts.append(reasoning)
                            if self.on_thinking_delta is not None:
                                self.on_thinking_delta(reasoning)
                        text = delta.get("content")
                        if isinstance(text, str) and text:
                            received_delta = True
                            content_parts.append(text)
                return _StreamChatResult(
                    content="".join(content_parts),
                    saw_done=saw_done,
                    finish_reason=finish_reason,
                )
            except _StreamStatusError as exc:
                if attempt == attempts or not is_retryable_status_code(exc.status_code):
                    raise AnswerGenerationError(str(exc)) from exc
            except ANSWER_STREAM_RETRYABLE_TRANSPORT_ERRORS as exc:
                if received_delta:
                    return _StreamChatResult(
                        content="".join(content_parts),
                        saw_done=False,
                        finish_reason=finish_reason,
                        stream_error=f"流式连接中断: {type(exc).__name__}",
                    )
                if attempt == attempts:
                    raise
            sleep_before_retry(attempt, self.retry_base_delay)
        raise AnswerGenerationError("chat/completions 流式重试次数耗尽")


def _sse_data(line: object) -> str | None:
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    if not isinstance(line, str):
        return None
    stripped = line.strip()
    if not stripped.startswith("data:"):
        return None
    return stripped.removeprefix("data:").strip()


def _chunk_event(data: str) -> tuple[dict[str, Any], str | None]:
    payload = json.loads(data)
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        return {}, None
    choice = choices[0]
    if not isinstance(choice, dict):
        return {}, None
    delta = choice.get("delta")
    finish_reason = choice.get("finish_reason")
    normalized_finish_reason = (
        finish_reason
        if isinstance(finish_reason, str) or finish_reason is None
        else "other"
    )
    return (
        delta if isinstance(delta, dict) else {},
        normalized_finish_reason,
    )


def _require_complete_stream(result: _StreamChatResult) -> None:
    if result.stream_error:
        raise ValueError(result.stream_error)
    if not result.saw_done:
        raise ValueError("流式响应未收到 [DONE]")
    if result.finish_reason not in (None, "stop"):
        raise ValueError(
            "流式响应异常结束: "
            f"finish_reason={_safe_finish_reason_for_log(result.finish_reason)}"
        )


def _safe_finish_reason_for_log(finish_reason: str | None) -> str | None:
    if finish_reason in (None, "stop", "length", "content_filter"):
        return finish_reason
    return "other"


def _strip_json_fences(text: str) -> str:
    stripped = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    return match.group(1) if match else stripped


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


def _safe_error_fragment(value: object, limit: int) -> str:
    return _bounded_text(" ".join(str(value).split()), limit)


def _safe_model_error_summary(error: ValueError | RecursionError) -> str:
    if isinstance(error, json.JSONDecodeError):
        message = _safe_error_fragment(error.msg, ERROR_SUMMARY_CHARS // 2)
        return _bounded_text(
            "JSONDecodeError: "
            f"{message} line={error.lineno} column={error.colno} pos={error.pos}",
            ERROR_SUMMARY_CHARS,
        )

    if isinstance(error, ValidationError):
        details: list[str] = []
        for item in error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        ):
            error_type = _safe_error_fragment(
                item.get("type", "validation_error"),
                100,
            )
            location = item.get("loc", ())
            if isinstance(location, (list, tuple)):
                safe_location = ".".join(
                    _safe_error_fragment(part, 100) for part in location
                )
            else:
                safe_location = _safe_error_fragment(location, 100)
            safe_location = safe_location or "<root>"
            if error_type in {"value_error", "assertion_error"}:
                message = "模型输出未通过自定义字段校验"
            else:
                message = _safe_error_fragment(
                    item.get("msg", "模型输出字段无效"),
                    200,
                )
            details.append(
                f"type={error_type} loc={safe_location} msg={message}"
            )
        detail_text = "; ".join(details) or "模型输出未通过结构校验"
        return _bounded_text(
            f"ValidationError: {detail_text}",
            ERROR_SUMMARY_CHARS,
        )

    if isinstance(error, RecursionError):
        return "RecursionError: 模型输出 JSON 嵌套层级过深"

    return _bounded_text(
        f"ValueError: {_safe_error_fragment(error, ERROR_SUMMARY_CHARS)}",
        ERROR_SUMMARY_CHARS,
    )


def _retry_messages(
    base_messages: list[dict[str, str]],
    *,
    invalid_content: str,
    error: ValueError | RecursionError,
) -> list[dict[str, str]]:
    error_summary = _safe_model_error_summary(error)
    invalid_excerpt = _bounded_text(
        invalid_content,
        INVALID_OUTPUT_EXCERPT_CHARS,
    )
    return [
        *base_messages,
        {"role": "assistant", "content": invalid_excerpt},
        {
            "role": "user",
            "content": (
                "上一次输出无法作为完整回答解析。\n"
                f"错误：{error_summary}\n"
                "请从头重新生成完整 JSON，不要续写上一次内容。"
                "只能输出包含 answer 和 citation_indexes 字段的 JSON object；"
                "answer 必须是非空字符串，citation_indexes 必须是整数数组。"
            ),
        },
    ]


def answer_query(
    *,
    query: str,
    query_agent: _QueryAgent,
    embedding_client: _EmbeddingClient,
    store: _Store,
    reranker: _Reranker,
    answer_agent: _AnswerAgent,
    top_n: int,
    top_k: int,
    trace: TraceSink | None = None,
    understanding: QueryUnderstanding | None = None,
    query_understanding_traces_emitted: bool = False,
) -> dict[str, Any]:
    search_result = search_evidence(
        query=query,
        query_agent=query_agent,
        embedding_client=embedding_client,  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        reranker=reranker,  # type: ignore[arg-type]
        top_n=top_n,
        top_k=top_k,
        trace=trace,
        understanding=understanding,
        query_understanding_traces_emitted=query_understanding_traces_emitted,
    )
    return answer_from_search_result(
        search_result,
        answer_agent=answer_agent,
        trace=trace,
    )


def answer_from_search_result(
    search_result: dict[str, Any],
    *,
    answer_agent: _AnswerAgent,
    trace: TraceSink | None = None,
) -> dict[str, Any]:
    evidence_count = len(search_result.get("results", []) or [])
    if evidence_count == 0:
        emit_trace(
            trace,
            "answer.skipped",
            {"reason": "检索结果为空", "evidence_count": 0},
        )
        return _answer_payload(
            search_result,
            answer="当前检索结果不足以确认。",
            citations=[],
            evidence_count=0,
            reasoning="",
        )

    generated = answer_agent.generate(search_result)
    citations = _citations_with_evidence_fallback(
        search_result,
        _selected_citations(
            _all_citations(search_result),
            getattr(generated, "citation_indexes", []),
        ),
    )
    answer = _strip_inline_citation_markers(str(getattr(generated, "answer")))
    reasoning = str(getattr(generated, "reasoning", "") or "")
    emit_trace(
        trace,
        "answer.generated",
        {
            "evidence_count": evidence_count,
            "citation_count": len(citations),
            "compliance_risks": _collect_compliance_risks(search_result),
            "reasoning_chars": len(reasoning),
            "answer_preview": _preview(answer),
        },
    )
    return _answer_payload(
        search_result,
        answer=answer,
        citations=citations,
        evidence_count=evidence_count,
        reasoning=reasoning,
    )


def _answer_payload(
    search_result: dict[str, Any],
    *,
    answer: str,
    citations: list[dict[str, Any]],
    evidence_count: int,
    reasoning: str,
) -> dict[str, Any]:
    return {
        "original_query": search_result.get("original_query", ""),
        "rewritten_query": search_result.get("rewritten_query", ""),
        "intent": search_result.get("intent", ""),
        "filters": search_result.get("filters", {}),
        "answer": answer,
        "reasoning": reasoning,
        "citations": citations,
        "evidence_count": evidence_count,
    }


def _build_user_prompt(search_result: dict[str, Any]) -> str:
    lines = [
        f"原始问题：{search_result.get('original_query', '')}",
        f"改写问题：{search_result.get('rewritten_query', '')}",
        "",
        "检索证据：",
        _evidence_text(search_result),
    ]
    compliance_block = _compliance_guidance_text(search_result)
    if compliance_block:
        lines.extend(["", compliance_block])
    return "\n".join(lines)


# 打标规则给证据标注的合规风险 → 回答生成时的定向约束。
# 键与 tagging._COMPLIANCE_RISK_RULES 的标签值一致；未收录的风险走通用兜底文案。
_COMPLIANCE_GUIDANCE = {
    "收益承诺风险": "不得出现保证收益、稳赚、一定有收益等承诺性表述。",
    "夸大保障风险": "不得使用什么都保、全覆盖等夸大保障范围的表述。",
    "理赔承诺风险": "不得承诺一定赔付或任何理赔结果。",
    "适当性风险": "不得使用最好、唯一、一定适合等绝对化推荐表述。",
    "医疗建议风险": "不得给出诊断、治疗、用药等医疗建议。",
    "税务法律建议风险": "不得提供避税、税务筹划或法律安排建议。",
    "隐私信息风险": "不得复述身份证、手机号、银行卡等客户隐私信息。",
    "竞品比较风险": "不得贬低或否定其他公司及其产品。",
    "误导销售风险": "不得出现返佣、返钱或夸大利益的误导表述。",
    "产品适配风险": "不得宣称产品适合所有人。",
    "合规与风控领域": (
        "证据属于合规与风控领域，回答需保留原文限制条件，不得扩展承诺。"
    ),
}


def _collect_compliance_risks(search_result: dict[str, Any]) -> list[str]:
    risks: list[str] = []
    seen: set[str] = set()
    for item in search_result.get("results", []) or []:
        metadata = item.get("metadata") or {}
        values = metadata.get("compliance_risks") or []
        if isinstance(values, list):
            for value in values:
                text = str(value).strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                risks.append(text)
        domain_tags = metadata.get("domain_tags") or []
        is_compliance_domain = metadata.get("primary_domain") == "合规与风控" or (
            isinstance(domain_tags, list) and "合规与风控" in domain_tags
        )
        if is_compliance_domain and "合规与风控领域" not in seen:
            seen.add("合规与风控领域")
            risks.append("合规与风控领域")
    return risks


def _compliance_guidance_text(search_result: dict[str, Any]) -> str:
    risks = _collect_compliance_risks(search_result)
    if not risks:
        return ""
    lines = ["合规注意（回答必须遵守）："]
    for risk in risks:
        guidance = _COMPLIANCE_GUIDANCE.get(
            risk, "回答需谨慎表述，不得作出无证据支持的承诺。"
        )
        lines.append(f"- 证据涉及{risk}：{guidance}")
    return "\n".join(lines)


def _evidence_text(search_result: dict[str, Any]) -> str:
    lines: list[str] = []
    citation_index = 1
    for evidence_index, item in enumerate(search_result.get("results", []) or [], start=1):
        lines.extend(
            [
                f"[证据{evidence_index}]",
                f"chunk_id：{item.get('chunk_id', '')}",
                f"知识类型：{item.get('chunk_type', '')}",
                f"内容：{item.get('text', '')}",
            ]
        )
        citations = item.get("citations", []) or []
        for citation in citations:
            lines.append(
                "[引用{index}] 文件：{filename}；章节：{section}；位置：{location}；原文：{quote}".format(
                    index=citation_index,
                    filename=citation.get("filename", ""),
                    section=citation.get("section_name", ""),
                    location=_citation_location_text(citation),
                    quote=citation.get("source_excerpt")
                    or citation.get("quote", ""),
                )
            )
            citation_index += 1
        lines.append("")
    return "\n".join(lines).strip()


def _all_citations(search_result: dict[str, Any]) -> list[dict[str, Any]]:
    # evidence_index 标注引用属于第几条证据（1-based），供 UI 把引用挂回证据卡片。
    citations: list[dict[str, Any]] = []
    for evidence_index, item in enumerate(
        search_result.get("results", []) or [], start=1
    ):
        for citation in item.get("citations", []) or []:
            citations.append({**citation, "evidence_index": evidence_index})
    return citations


def _selected_citations(
    citations: list[dict[str, Any]],
    citation_indexes: list[int],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_indexes: set[int] = set()
    for index in citation_indexes:
        if index in seen_indexes or index < 1 or index > len(citations):
            continue
        seen_indexes.add(index)
        selected.append({**citations[index - 1], "selected": True})
    return selected


def _citations_with_evidence_fallback(
    search_result: dict[str, Any],
    selected: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # 模型选中的引用标 selected=True；为溯源完整性兜底补齐的引用标 selected=False，
    # UI 据此收敛展示而不丢失完整引用数据。
    citations = list(selected)
    seen_keys = {_citation_key(citation) for citation in citations}
    for evidence_index, item in enumerate(
        search_result.get("results", []) or [], start=1
    ):
        for citation in item.get("citations", []) or []:
            if not isinstance(citation, dict):
                continue
            key = _citation_key(citation)
            if key in seen_keys:
                continue
            citations.append(
                {**citation, "evidence_index": evidence_index, "selected": False}
            )
            seen_keys.add(key)
    return citations


def _citation_key(citation: dict[str, Any]) -> tuple[Any, ...]:
    return (
        citation.get("source_path"),
        citation.get("filename"),
        citation.get("section_name"),
        json.dumps(citation.get("locator") or {}, sort_keys=True, default=str),
        citation.get("source_excerpt") or citation.get("quote"),
    )


def _preview(text: str, limit: int = 120) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "..."


def _strip_inline_citation_markers(text: str) -> str:
    return re.sub(r"\[引用\d+\]", "", text).strip()


def _citation_location_text(citation: dict[str, Any]) -> str:
    locator = citation.get("locator") or {}
    if not isinstance(locator, dict):
        return ""
    parts: list[str] = []
    if locator.get("page"):
        parts.append(f"p{locator['page']}")
    if locator.get("slide"):
        parts.append(f"slide{locator['slide']}")
    line_start = locator.get("line_start")
    line_end = locator.get("line_end")
    if line_start and line_end:
        parts.append(f"L{line_start}-L{line_end}")
    heading_path = locator.get("heading_path")
    if isinstance(heading_path, list) and heading_path:
        parts.append(" > ".join(str(item) for item in heading_path))
    if citation.get("anchor_id"):
        parts.append(str(citation["anchor_id"]))
    return " / ".join(parts)


_SYSTEM_PROMPT = """你是保险销售洞察 RAG 的回答整合节点。
请只根据用户问题和给定检索证据生成中文回答，不要使用证据外信息。
如果证据不足以回答，answer 必须说明“当前检索结果不足以确认”。
输出 JSON object，字段：
- answer: 面向用户的自然语言答案，简洁、可执行，必要时分点；不要在正文中输出 [引用N] 标记。
- citation_indexes: 支撑答案的引用编号数组，只能选择用户消息里的 [引用N] 编号。
要求：
1. 不要编造产品条款、监管要求、收益承诺或案例细节。
2. 不要把销售侧收益误写成客户侧收益，除非证据明确支持。
3. 回答中可以综合多条证据，但每个关键结论必须能被选中的引用支持。
4. 如果引用只支持部分结论，降低表达强度并说明边界。
"""
