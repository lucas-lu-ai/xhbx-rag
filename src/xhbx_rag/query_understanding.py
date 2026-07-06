from __future__ import annotations

import json
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .http_retry import post_json_with_retry


Intent = Literal[
    "script_search",
    "objection_handling",
    "strategy_search",
    "journey_search",
    "general_sales_qa",
    "out_of_scope",
]

_ALLOWED_CHUNK_TYPES = {
    "customer_journey",
    "strategy",
    "script",
    "objection_handling",
    "knowledge_entry",
}


class QueryUnderstandingError(RuntimeError):
    """Raised when query understanding cannot produce a valid retrieval plan."""


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


class QueryFilters(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chunk_types: list[str] = Field(default_factory=list)
    stage: str = ""
    scenario: str = ""
    objection: str = ""
    strategy_names: list[str] = Field(default_factory=list)

    @field_validator("chunk_types", mode="before")
    @classmethod
    def _chunk_types(cls, value: object) -> list[str]:
        return [
            item
            for item in _str_list(value)
            if item in _ALLOWED_CHUNK_TYPES
        ]

    @field_validator("strategy_names", mode="before")
    @classmethod
    def _str_list(cls, value: object) -> list[str]:
        return _str_list(value)

    @field_validator("stage", "scenario", "objection", mode="before")
    @classmethod
    def _str_value(cls, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            values = [str(item).strip() for item in value if str(item).strip()]
            return values[0] if len(values) == 1 else ""
        return str(value).strip()


class QueryUnderstanding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Intent
    rewritten_query: str = ""
    needs_retrieval: bool = True
    filters: QueryFilters = Field(default_factory=QueryFilters)

    @model_validator(mode="after")
    def _require_rewritten_query(self) -> "QueryUnderstanding":
        if self.needs_retrieval and not self.rewritten_query.strip():
            raise ValueError("needs_retrieval=true 时 rewritten_query 不能为空")
        return self


class QueryUnderstandingAgent:
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

    def understand(self, query: str) -> QueryUnderstanding:
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
                    {
                        "role": "system",
                        "content": _SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": query,
                    },
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
            return QueryUnderstanding.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - normalize parser/model errors
            raise QueryUnderstandingError(f"query understanding 解析失败: {exc}") from exc


def _extract_content(payload: dict) -> str:
    choices = payload.get("choices")
    if not choices:
        raise QueryUnderstandingError("chat/completions 响应缺少 choices")
    message = choices[0].get("message", {})
    content = message.get("content")
    if not isinstance(content, str):
        raise QueryUnderstandingError("chat/completions 响应缺少 message.content")
    return content


_SYSTEM_PROMPT = """你是销售洞察 RAG 的查询理解节点。
请把用户原始问题转换成 JSON，只输出 JSON object。
字段：
- intent: script_search | objection_handling | strategy_search | journey_search | general_sales_qa | out_of_scope
- rewritten_query: 独立、明确、适合检索的问题；如果 needs_retrieval=false 可为空
- needs_retrieval: boolean
- filters: object，包含 chunk_types, stage, scenario, objection, strategy_names
  - chunk_types 只能使用 customer_journey | strategy | script | objection_handling | knowledge_entry，不要输出 qa。
要求：
1. 不要增加用户没有表达的事实约束。
2. 不能直接回答用户问题。
3. 如果问题不属于保险销售洞察、话术、异议、策略或客户旅程，intent=out_of_scope 且 needs_retrieval=false。
4. 通用解释、作用、价值、原因类问题使用 general_sales_qa，chunk_types 通常留空，除非用户明确限定只查话术、策略、异议、客户旅程或知识条目（knowledge_entry）。
"""


def _str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []
