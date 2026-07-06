from __future__ import annotations

import json
import re
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .http_retry import post_json_with_retry
from .observability import TraceSink, emit_trace
from .search import search_evidence


class AnswerGenerationError(RuntimeError):
    """Raised when answer generation cannot produce a valid grounded answer."""


class _HttpClient(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: dict,
        json: dict,
        timeout: float,
    ) -> object:
        """Post JSON to an API endpoint."""


class _QueryAgent(Protocol):
    def understand(self, query: str) -> object:
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
            except (TypeError, ValueError):
                continue
            if index > 0:
                indexes.append(index)
        return indexes

    @model_validator(mode="after")
    def _require_answer(self) -> "GeneratedAnswer":
        if not self.answer.strip():
            raise ValueError("answer 不能为空")
        return self


class AnswerAgent:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        http_client: _HttpClient | None = None,
        timeout: float = 60.0,
        retry_attempts: int = 3,
        retry_base_delay: float = 0.5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.http_client = http_client or httpx.Client()
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_base_delay = retry_base_delay

    def generate(self, search_result: dict[str, Any]) -> GeneratedAnswer:
        response = post_json_with_retry(
            self.http_client,
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_prompt(search_result)},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            timeout=self.timeout,
            retry_attempts=self.retry_attempts,
            retry_base_delay=self.retry_base_delay,
        )
        payload = response.json()  # type: ignore[attr-defined]
        content = _extract_content(payload)
        try:
            data = json.loads(content)
            return GeneratedAnswer.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - normalize parser/model errors
            raise AnswerGenerationError(f"answer generation 解析失败: {exc}") from exc


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
) -> dict[str, Any]:
    search_result = search_evidence(
        query=query,
        query_agent=query_agent,  # type: ignore[arg-type]
        embedding_client=embedding_client,  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        reranker=reranker,  # type: ignore[arg-type]
        top_n=top_n,
        top_k=top_k,
        trace=trace,
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
    emit_trace(
        trace,
        "answer.generated",
        {
            "evidence_count": evidence_count,
            "citation_count": len(citations),
            "compliance_risks": _collect_compliance_risks(search_result),
            "answer_preview": _preview(answer),
        },
    )
    return _answer_payload(
        search_result,
        answer=answer,
        citations=citations,
        evidence_count=evidence_count,
    )


def _answer_payload(
    search_result: dict[str, Any],
    *,
    answer: str,
    citations: list[dict[str, Any]],
    evidence_count: int,
) -> dict[str, Any]:
    return {
        "original_query": search_result.get("original_query", ""),
        "rewritten_query": search_result.get("rewritten_query", ""),
        "intent": search_result.get("intent", ""),
        "filters": search_result.get("filters", {}),
        "answer": answer,
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
}


def _collect_compliance_risks(search_result: dict[str, Any]) -> list[str]:
    risks: list[str] = []
    seen: set[str] = set()
    for item in search_result.get("results", []) or []:
        metadata = item.get("metadata") or {}
        values = metadata.get("compliance_risks") or []
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            risks.append(text)
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


def _extract_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not choices:
        raise AnswerGenerationError("chat/completions 响应缺少 choices")
    message = choices[0].get("message", {})
    content = message.get("content")
    if not isinstance(content, str):
        raise AnswerGenerationError("chat/completions 响应缺少 message.content")
    return content


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
