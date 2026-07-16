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
CollectionTarget = Literal["case", "course"]

_CASE_CHUNK_TYPES = frozenset(
    {"customer_journey", "strategy", "script", "objection_handling"}
)
_COURSE_CHUNK_TYPES = frozenset({"training_course", "knowledge_entry"})
_CASE_ONLY_INTENTS = frozenset({"journey_search", "objection_handling"})
_ALLOWED_CHUNK_TYPES = {
    *_CASE_CHUNK_TYPES,
    *_COURSE_CHUNK_TYPES,
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
    collection_targets: list[CollectionTarget] = Field(
        default_factory=lambda: ["case", "course"]
    )
    filters: QueryFilters = Field(default_factory=QueryFilters)

    @field_validator("collection_targets", mode="before")
    @classmethod
    def _collection_targets(cls, value: object) -> list[str]:
        targets: list[str] = []
        for item in _str_list(value):
            if item in {"case", "course"} and item not in targets:
                targets.append(item)
        return targets or ["case", "course"]

    @model_validator(mode="after")
    def _require_rewritten_query(self) -> "QueryUnderstanding":
        if self.needs_retrieval and not self.rewritten_query.strip():
            raise ValueError("needs_retrieval=true 时 rewritten_query 不能为空")
        return self

    @model_validator(mode="after")
    def _reconcile_collection_targets(self) -> "QueryUnderstanding":
        if not self.needs_retrieval or self.intent == "out_of_scope":
            return self

        chunk_types = set(self.filters.chunk_types)
        needs_case = self.intent in _CASE_ONLY_INTENTS or bool(
            chunk_types.intersection(_CASE_CHUNK_TYPES)
        )
        needs_course = bool(chunk_types.intersection(_COURSE_CHUNK_TYPES))
        targets = set(self.collection_targets)
        if (needs_case and "case" not in targets) or (
            needs_course and "course" not in targets
        ):
            self.collection_targets = ["case", "course"]
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
- collection_targets: 必须输出的字符串数组，只能使用 case | course
  - case 包含 customer_journey | strategy | script | objection_handling
  - course 包含 training_course | knowledge_entry
  - 案例实战、绩优经验类问题选择 ["case"]
  - 课程教材、标准流程类问题选择 ["course"]
  - 同时需要案例和课程时选择 ["case", "course"]
  - 无法确定时选择 ["case", "course"]
  - case/course 是来源语义，不代表两个物理 collection
- filters: object，包含 chunk_types, stage, scenario, objection, strategy_names
  - chunk_types 只能使用 customer_journey | strategy | script | objection_handling | training_course | knowledge_entry，不要输出 qa。
要求：
1. 不要增加用户没有表达的事实约束。
2. 不能直接回答用户问题。
3. 如果问题不属于保险销售知识（案例经验、话术、异议、策略、客户旅程、培训课程），intent=out_of_scope 且 needs_retrieval=false。
4. 通用解释、作用、价值、原因类问题使用 general_sales_qa，chunk_types 通常留空，除非用户明确限定只查某类知识。
5. 知识库同时包含绩优案例经验与制式培训资料两类：问"怎么讲这门课/课程内容/标准流程/制式话术/培训教材"倾向 chunk_types=["training_course", "knowledge_entry"]；问"某位绩优如何做成/实战经验"倾向案例四类；不确定时 chunk_types 留空同时检索两类。
6. collection_targets 必须与 intent 和 filters.chunk_types 一致；混合类型选择两者，不确定时也选择两者。
7. script_search 和 strategy_search 本身不足以决定 collection_targets；制式话术、标准流程可属于 course。
8. 结合用户问题、filters.chunk_types 和 collection_targets 判断目标库，确保目标与 chunk_types 一致。
"""


def _str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []
