"""课程级 LLM 增值：每门课一次小调用，产出摘要、受众与销售环节标签。

切块层只消费 `CourseEnrichment` 数据对象；生产实现复用案例管线的
AgentScope 结构化输出自修复回路（tool-call 强制 + 校验失败重试）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, field_validator

from .sales_generation import (
    _build_structured_chat_model,
    _call_agent_scope_structured_output_async,
)

_SAMPLE_TEXT_MAX_CHARS = 4000

_AUDIENCE_KEYWORDS = (
    ("新人", "新人"),
    ("主管", "主管"),
    ("经理", "主管"),
    ("绩优", "绩优"),
    ("讲师", "讲师"),
    ("师资", "讲师"),
    ("通用", "通用"),
)

_SYSTEM_PROMPT = (
    "你是保险培训课程的知识整理助手。根据课件的课名、所属课程体系与抽样文本，"
    "生成课程摘要（2-3 句，说明这门课讲什么、解决什么问题）、适用对象"
    "（新人/主管/绩优/讲师/通用 之一）与涉及的销售环节标签。"
    "只依据给定文本，不要虚构内容。"
)


@dataclass(frozen=True)
class CourseEnrichment:
    """一门课的 LLM 增值产物：摘要、受众与销售环节标签。"""

    summary: str = ""
    audience: str = ""
    sales_stages: tuple[str, ...] = ()


class _CourseEnrichmentDraft(BaseModel):
    """面向 LLM 输出的宽松契约。"""

    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    audience: str = ""
    sales_stages: list[str] = []

    @field_validator("audience", mode="before")
    @classmethod
    def _coerce_audience(cls, value: object) -> str:
        text = str(value or "").strip()
        for keyword, audience in _AUDIENCE_KEYWORDS:
            if keyword in text:
                return audience
        return ""

    @field_validator("sales_stages", mode="before")
    @classmethod
    def _coerce_sales_stages(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []


class CourseEnrichmentAgent(Protocol):
    def enrich(
        self, course_name: str, course_series: str, sample_text: str
    ) -> CourseEnrichment:
        """生成一门课的增值信息。"""


class CourseEnrichmentAgentScopeAgent:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        chat_model: Any | None = None,
        timeout: float = 600.0,
        retry_attempts: int = 5,
        retry_base_delay: float = 1.0,
        enable_thinking: bool = False,
    ) -> None:
        self.chat_model = chat_model or _build_structured_chat_model(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model=model,
            timeout=timeout,
            retry_attempts=retry_attempts,
            retry_base_delay=retry_base_delay,
            enable_thinking=enable_thinking,
        )

    def enrich(
        self, course_name: str, course_series: str, sample_text: str
    ) -> CourseEnrichment:
        return asyncio.run(self.enrich_async(course_name, course_series, sample_text))

    async def enrich_async(
        self, course_name: str, course_series: str, sample_text: str
    ) -> CourseEnrichment:
        from agentscope.message import SystemMsg, UserMsg

        user_prompt = "\n".join(
            [
                f"课程名称：{course_name}",
                f"课程体系：{course_series}" if course_series else "",
                "课件抽样文本：",
                sample_text[:_SAMPLE_TEXT_MAX_CHARS],
            ]
        )
        payload = await _call_agent_scope_structured_output_async(
            self.chat_model,
            [
                SystemMsg(name="system", content=_SYSTEM_PROMPT),
                UserMsg(name="user", content=user_prompt),
            ],
            structured_model=_CourseEnrichmentDraft,
        )
        draft = _CourseEnrichmentDraft.model_validate(payload)
        return CourseEnrichment(
            summary=draft.summary.strip(),
            audience=draft.audience,
            sales_stages=tuple(draft.sales_stages),
        )
