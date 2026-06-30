from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
import inspect
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

import jsonschema
from agentscope._utils._common import _json_loads_with_repair
from agentscope.credential import DashScopeCredential, OpenAICredential
from agentscope.exception import ToolJSONDecodeError
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Base64Source, DataBlock, SystemMsg, TextBlock, UserMsg
from agentscope.model import DashScopeChatModel, OpenAIChatModel
from agentscope.tool import ToolChoice
from pydantic import BaseModel, ValidationError

from .models import (
    CaseSalesInsightsSource,
    EvidenceRef,
    SectionSalesEvidence,
)
from .observability import TraceSink, emit_trace
from .source_loader import (
    ParsedSourceFile,
    ParsedEmbeddedImage,
    SourceSection,
    enrich_evidence_refs,
    load_case_sections,
)


DEFAULT_SECTION_MATERIAL_MAX_CHARS = 18_000
_STRUCTURED_FUNC_NAME = "generate_structured_output"
_STRUCTURED_MAX_ATTEMPTS = 3
_STRUCTURED_INSTRUCTION = (
    f"<system-reminder>Now you **MUST** call the tool named "
    f"'{_STRUCTURED_FUNC_NAME}' to generate the structured output required "
    "by the user. Provide the fields directly as the tool arguments; do NOT "
    "wrap them in any extra key. DON'T do anything else.</system-reminder>"
)


class SalesInsightGenerationError(RuntimeError):
    """Raised when the sales-insight generation model response is invalid."""


class _AgentScopeChatModel(Protocol):
    async def __call__(self, messages: list[Any], **kwargs: Any) -> object:
        """Call an AgentScope chat model."""


class _SectionAgent(Protocol):
    def extract(self, section: SourceSection) -> SectionSalesEvidence:
        """Extract section-level sales evidence."""


class _AsyncSectionAgent(_SectionAgent, Protocol):
    async def extract_async(self, section: SourceSection) -> SectionSalesEvidence:
        """Extract section-level sales evidence asynchronously."""


class _CaseAgent(Protocol):
    def extract(
        self,
        case_name: str,
        evidences: list[SectionSalesEvidence],
    ) -> CaseSalesInsightsSource:
        """Extract case-level sales insights."""


class _AsyncCaseAgent(_CaseAgent, Protocol):
    async def extract_async(
        self,
        case_name: str,
        evidences: list[SectionSalesEvidence],
    ) -> CaseSalesInsightsSource:
        """Extract case-level sales insights asynchronously."""


class _VisionAgent(Protocol):
    def describe(
        self,
        image: ParsedEmbeddedImage,
        *,
        case_name: str,
        section_name: str,
        source: ParsedSourceFile,
    ) -> str:
        """Describe an embedded image as text."""


class _AsyncVisionAgent(_VisionAgent, Protocol):
    async def describe_async(
        self,
        image: ParsedEmbeddedImage,
        *,
        case_name: str,
        section_name: str,
        source: ParsedSourceFile,
    ) -> str:
        """Describe an embedded image as text asynchronously."""


@dataclass(frozen=True)
class CaseSalesGenerationResult:
    case_name: str
    status: str
    evidence_paths: tuple[Path, ...] = ()
    failure_paths: tuple[Path, ...] = ()
    insights_path: Path | None = None
    playbook_path: Path | None = None
    error: str | None = None


@dataclass(frozen=True)
class _SectionTaskSuccess:
    index: int
    evidence: SectionSalesEvidence
    path: Path


@dataclass(frozen=True)
class _SectionTaskFailure:
    index: int
    section_name: str
    path: Path
    error: str


class SalesInsightAgentScopeAgent:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        chat_model: _AgentScopeChatModel | None = None,
        timeout: float = 600.0,
        retry_attempts: int = 5,
        retry_base_delay: float = 1.0,
        max_section_chars: int = DEFAULT_SECTION_MATERIAL_MAX_CHARS,
        enable_thinking: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.chat_model = chat_model or _build_structured_chat_model(
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            timeout=timeout,
            retry_attempts=retry_attempts,
            retry_base_delay=retry_base_delay,
            enable_thinking=enable_thinking,
        )
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_base_delay = retry_base_delay
        self.max_section_chars = max_section_chars
        self.enable_thinking = enable_thinking

    def extract(self, *args: object) -> SectionSalesEvidence | CaseSalesInsightsSource:
        if len(args) == 1 and isinstance(args[0], SourceSection):
            return self.extract_section(args[0])
        if len(args) == 2 and isinstance(args[0], str) and isinstance(args[1], list):
            return self.extract_case(args[0], args[1])
        raise TypeError("extract 只支持 extract(section) 或 extract(case_name, evidences)")

    async def extract_async(
        self,
        *args: object,
    ) -> SectionSalesEvidence | CaseSalesInsightsSource:
        if len(args) == 1 and isinstance(args[0], SourceSection):
            return await self.extract_section_async(args[0])
        if len(args) == 2 and isinstance(args[0], str) and isinstance(args[1], list):
            return await self.extract_case_async(args[0], args[1])
        raise TypeError(
            "extract_async 只支持 extract_async(section) 或 "
            "extract_async(case_name, evidences)"
        )

    def extract_section(self, section: SourceSection) -> SectionSalesEvidence:
        data = self._generate_structured(
            system_prompt=_SECTION_SYSTEM_PROMPT,
            user_content=_render_section_material(
                section,
                max_chars=self.max_section_chars,
            ),
            structured_model=SectionSalesEvidence,
        )
        data = _fill_blank_identity(data, "case_name", section.case_name)
        data = _fill_blank_identity(data, "section_name", section.section_name)
        return SectionSalesEvidence.model_validate(data)

    async def extract_section_async(self, section: SourceSection) -> SectionSalesEvidence:
        data = await self._generate_structured_async(
            system_prompt=_SECTION_SYSTEM_PROMPT,
            user_content=_render_section_material(
                section,
                max_chars=self.max_section_chars,
            ),
            structured_model=SectionSalesEvidence,
        )
        data = _fill_blank_identity(data, "case_name", section.case_name)
        data = _fill_blank_identity(data, "section_name", section.section_name)
        return SectionSalesEvidence.model_validate(data)

    def extract_case(
        self,
        case_name: str,
        evidences: list[SectionSalesEvidence],
    ) -> CaseSalesInsightsSource:
        data = self._generate_structured(
            system_prompt=_CASE_SYSTEM_PROMPT,
            user_content=_render_case_sales_evidence(case_name, evidences),
            structured_model=CaseSalesInsightsSource,
        )
        data = _fill_blank_identity(data, "case_name", case_name)
        return CaseSalesInsightsSource.model_validate(data)

    async def extract_case_async(
        self,
        case_name: str,
        evidences: list[SectionSalesEvidence],
    ) -> CaseSalesInsightsSource:
        data = await self._generate_structured_async(
            system_prompt=_CASE_SYSTEM_PROMPT,
            user_content=_render_case_sales_evidence(case_name, evidences),
            structured_model=CaseSalesInsightsSource,
        )
        data = _fill_blank_identity(data, "case_name", case_name)
        return CaseSalesInsightsSource.model_validate(data)

    def _generate_structured(
        self,
        *,
        system_prompt: str,
        user_content: str,
        structured_model: type[BaseModel] | dict[str, Any],
    ) -> dict[str, Any]:
        return _call_agent_scope_structured_output(
            self.chat_model,
            [
                SystemMsg(name="system", content=system_prompt),
                UserMsg(name="user", content=user_content),
            ],
            structured_model=structured_model,
        )

    async def _generate_structured_async(
        self,
        *,
        system_prompt: str,
        user_content: str,
        structured_model: type[BaseModel] | dict[str, Any],
    ) -> dict[str, Any]:
        return await _call_agent_scope_structured_output_async(
            self.chat_model,
            [
                SystemMsg(name="system", content=system_prompt),
                UserMsg(name="user", content=user_content),
            ],
            structured_model=structured_model,
        )


SalesInsightHttpAgent = SalesInsightAgentScopeAgent


class VisionImageDescriptionAgent:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        chat_model: _AgentScopeChatModel | None = None,
        timeout: float = 90.0,
        retry_attempts: int = 5,
        retry_base_delay: float = 1.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.chat_model = chat_model or _build_dashscope_chat_model(
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            timeout=timeout,
            retry_attempts=retry_attempts,
            retry_base_delay=retry_base_delay,
            enable_thinking=False,
        )

    def describe(
        self,
        image: ParsedEmbeddedImage,
        *,
        case_name: str,
        section_name: str,
        source: ParsedSourceFile,
    ) -> str:
        return asyncio.run(
            self.describe_async(
                image,
                case_name=case_name,
                section_name=section_name,
                source=source,
            )
        )

    async def describe_async(
        self,
        image: ParsedEmbeddedImage,
        *,
        case_name: str,
        section_name: str,
        source: ParsedSourceFile,
    ) -> str:
        prompt = "\n".join(
            [
                f"案例：{case_name}",
                f"章节：{section_name}",
                f"来源文件：{source.filename}",
                f"图片文件：{image.filename}",
                "请识别图片中的可见文字、表格、数字、流程和与保险销售相关的信息。",
                "只描述图片中能直接观察到的内容；不要推断图片外的信息。",
            ]
        )
        encoded = base64.b64encode(image.data).decode("ascii")
        text = await _call_agent_scope_model_text_async(
            self.chat_model,
            [
                SystemMsg(
                    name="system",
                    content=(
                        "你是保险销售案例素材的图片解析助手。"
                        "输出简洁中文描述，重点保留可见文字、数字、表格结构和销售场景线索。"
                    ),
                ),
                UserMsg(
                    name="user",
                    content=[
                        TextBlock(text=prompt),
                        DataBlock(
                            name=image.filename,
                            source=Base64Source(
                                data=encoded,
                                media_type=image.media_type,
                            ),
                        ),
                    ],
                ),
            ],
            max_tokens=1200,
        )
        return text.strip()


def generate_case_sales_insights(
    *,
    case_dir: Path,
    output_dir: Path,
    section_agent: _SectionAgent,
    case_agent: _CaseAgent | None = None,
    vision_agent: _VisionAgent | None = None,
    case_name: str | None = None,
    trace: TraceSink | None = None,
    section_concurrency: int = 1,
) -> CaseSalesGenerationResult:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            generate_case_sales_insights_async(
                case_dir=case_dir,
                output_dir=output_dir,
                section_agent=section_agent,
                case_agent=case_agent,
                vision_agent=vision_agent,
                case_name=case_name,
                trace=trace,
                section_concurrency=section_concurrency,
            )
        )
    raise RuntimeError("请在已运行的 event loop 内调用 generate_case_sales_insights_async")


async def generate_case_sales_insights_async(
    *,
    case_dir: Path,
    output_dir: Path,
    section_agent: _SectionAgent,
    case_agent: _CaseAgent | None = None,
    vision_agent: _VisionAgent | None = None,
    case_name: str | None = None,
    trace: TraceSink | None = None,
    section_concurrency: int = 1,
) -> CaseSalesGenerationResult:
    resolved_case_name = case_name or case_dir.name
    evidence_paths: list[Path] = []
    failure_paths: list[Path] = []
    try:
        sections = load_case_sections(case_dir, case_name=case_name)
        emit_trace(
            trace,
            "generate.sections_loaded",
            {"case_name": resolved_case_name, "section_count": len(sections)},
        )
        if not sections:
            return CaseSalesGenerationResult(
                case_name=resolved_case_name,
                status="failed",
                error=f"案例无可解析素材: {case_dir}",
            )
        sections = await _enrich_sections_with_image_descriptions_async(
            sections,
            vision_agent=vision_agent,
            trace=trace,
        )

        concurrency = max(1, section_concurrency)
        semaphore = asyncio.Semaphore(concurrency)
        tasks = [
            _run_section_generation_task(
                index,
                section,
                section_agent,
                output_dir=output_dir,
                trace=trace,
                semaphore=semaphore,
            )
            for index, section in enumerate(sections)
            if section.primary_text.strip()
        ]
        section_results = await asyncio.gather(*tasks)
        successes: list[_SectionTaskSuccess] = []
        for result in sorted(section_results, key=lambda item: item.index):
            if isinstance(result, _SectionTaskSuccess):
                successes.append(result)
                evidence_paths.append(result.path)
            elif isinstance(result, _SectionTaskFailure):
                failure_paths.append(result.path)

        evidences = [result.evidence for result in successes]

        if not evidences:
            return CaseSalesGenerationResult(
                case_name=resolved_case_name,
                status="failed",
                evidence_paths=tuple(evidence_paths),
                failure_paths=tuple(failure_paths),
                error=f"案例无可解析销售证据: {case_dir}",
            )

        all_sources = [source for section in sections for source in section.sources]
        case_agent = case_agent or section_agent  # type: ignore[assignment]
        raw_insights = await _extract_case_with_agent(
            case_agent,
            resolved_case_name,
            evidences,
        )
        insights = CaseSalesInsightsSource.model_validate(raw_insights)
        insights = insights.model_copy(
            update={"case_name": insights.case_name or resolved_case_name}
        )
        insights = _enrich_case_insights(insights, all_sources)
        write_result = _write_case_sales_insights(insights, output_dir)
        emit_trace(
            trace,
            "generate.case_insights_written",
            {
                "insights_path": str(write_result.insights_path),
                "playbook_path": str(write_result.playbook_path),
            },
        )
        return CaseSalesGenerationResult(
            case_name=resolved_case_name,
            status="ok",
            evidence_paths=tuple(evidence_paths),
            failure_paths=tuple(failure_paths),
            insights_path=write_result.insights_path,
            playbook_path=write_result.playbook_path,
        )
    except Exception as exc:  # noqa: BLE001 - keep CLI result JSON stable
        return CaseSalesGenerationResult(
            case_name=resolved_case_name,
            status="failed",
            evidence_paths=tuple(evidence_paths),
            failure_paths=tuple(failure_paths),
            error=repr(exc),
        )


def _build_dashscope_chat_model(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    retry_attempts: int,
    retry_base_delay: float,
    enable_thinking: bool,
) -> DashScopeChatModel:
    return DashScopeChatModel(
        credential=DashScopeCredential(api_key=api_key, base_url=base_url),
        model=model,
        parameters=DashScopeChatModel.Parameters(
            temperature=0,
            thinking_enable=enable_thinking,
        ),
        stream=False,
        max_retries=max(0, retry_attempts - 1),
        retry_delay=retry_base_delay,
        client_kwargs={"timeout": timeout},
    )


def _build_structured_chat_model(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    retry_attempts: int,
    retry_base_delay: float,
    enable_thinking: bool,
) -> OpenAIChatModel:
    return OpenAIChatModel(
        credential=OpenAICredential(api_key=api_key, base_url=base_url),
        model=model,
        parameters=OpenAIChatModel.Parameters(temperature=0),
        formatter=OpenAIChatFormatter(),
        stream=False,
        max_retries=max(0, retry_attempts - 1),
        retry_delay=retry_base_delay,
        client_kwargs={"timeout": timeout},
        extra_body={"enable_thinking": enable_thinking},
    )


def _call_agent_scope_model_text(
    chat_model: _AgentScopeChatModel,
    messages: list[Any],
    **kwargs: Any,
) -> str:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_call_agent_scope_model_text_async(chat_model, messages, **kwargs))
    raise RuntimeError("同步 CLI 中不支持在已运行的 event loop 内调用 AgentScope 模型")


async def _call_agent_scope_model_text_async(
    chat_model: _AgentScopeChatModel,
    messages: list[Any],
    **kwargs: Any,
) -> str:
    response = await chat_model(messages, **kwargs)
    if inspect.isasyncgen(response):
        completed = None
        async for chunk in response:
            if getattr(chunk, "is_last", False):
                completed = chunk
        response = completed
    if response is None:
        raise SalesInsightGenerationError("chat/completions 响应缺少最终消息")
    text = _extract_agent_scope_text(response)
    if not text:
        raise SalesInsightGenerationError("chat/completions 响应缺少文本内容")
    return text


def _extract_agent_scope_text(response: object) -> str:
    if isinstance(response, str):
        return response
    blocks = getattr(response, "content", None) or []
    texts: list[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            texts.append(text)
    return "\n".join(texts)


def _call_agent_scope_structured_output(
    chat_model: _AgentScopeChatModel,
    messages: list[Any],
    *,
    structured_model: type[BaseModel] | dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            _call_agent_scope_structured_output_async(
                chat_model,
                messages,
                structured_model=structured_model,
                **kwargs,
            )
        )
    raise RuntimeError("同步 CLI 中不支持在已运行的 event loop 内调用 AgentScope 模型")


async def _call_agent_scope_structured_output_async(
    chat_model: _AgentScopeChatModel,
    messages: list[Any],
    *,
    structured_model: type[BaseModel] | dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    schema = _schema_of(structured_model)
    tools = [
        {
            "type": "function",
            "function": {
                "name": _STRUCTURED_FUNC_NAME,
                "description": "Call this function to generate structured output required by the user.",
                "parameters": schema,
            },
        }
    ]
    convo = _messages_with_structured_reminder(messages)
    last_error: Exception | None = None
    for attempt in range(_STRUCTURED_MAX_ATTEMPTS):
        response = await chat_model(
            convo,
            tools=tools,
            tool_choice=ToolChoice(mode="auto"),
            **kwargs,
        )
        if inspect.isasyncgen(response):
            completed = None
            async for chunk in response:
                if getattr(chunk, "is_last", False):
                    completed = chunk
            response = completed
        if response is None:
            last_error = SalesInsightGenerationError("chat/completions 响应缺少最终消息")
            if attempt < _STRUCTURED_MAX_ATTEMPTS - 1:
                convo = _messages_with_structured_repair_feedback(
                    convo,
                    error=last_error,
                    previous_output="",
                )
            continue
        previous_output = _summarize_structured_response(response)
        try:
            data = _extract_tool_args(response, schema)
        except (ToolJSONDecodeError, ValueError) as exc:
            last_error = exc
            if attempt < _STRUCTURED_MAX_ATTEMPTS - 1:
                convo = _messages_with_structured_repair_feedback(
                    convo,
                    error=exc,
                    previous_output=previous_output,
                )
            continue
        if data is None:
            last_error = SalesInsightGenerationError("模型未调用结构化输出工具")
            if attempt < _STRUCTURED_MAX_ATTEMPTS - 1:
                convo = _messages_with_structured_repair_feedback(
                    convo,
                    error=last_error,
                    previous_output=previous_output,
                )
            continue
        data = _unwrap_single_key(data, structured_model)
        try:
            _validate_structured_output(data, structured_model)
        except (ValidationError, jsonschema.ValidationError) as exc:
            last_error = exc
            if attempt < _STRUCTURED_MAX_ATTEMPTS - 1:
                convo = _messages_with_structured_repair_feedback(
                    convo,
                    error=exc,
                    previous_output=json.dumps(
                        data,
                        ensure_ascii=False,
                        default=str,
                    ),
                )
            continue
        return data
    raise last_error or SalesInsightGenerationError("结构化输出失败")


def _messages_with_structured_reminder(messages: list[Any]) -> list[Any]:
    copied = deepcopy(messages)
    reminder = TextBlock(text=_STRUCTURED_INSTRUCTION)
    if copied and getattr(copied[-1], "role", "") == "user":
        copied[-1].content = copied[-1].get_content_blocks() + [reminder]
    else:
        copied.append(UserMsg(name="user", content=[reminder]))
    return copied


def _messages_with_structured_repair_feedback(
    messages: list[Any],
    *,
    error: Exception,
    previous_output: str,
) -> list[Any]:
    feedback = [
        "上一次结构化输出校验失败。",
        "请基于原始任务重新调用结构化输出工具，并修复以下问题。",
        f"校验错误：{repr(error)}",
    ]
    if previous_output.strip():
        feedback.extend(
            [
                "上一次模型输出：",
                previous_output.strip()[:6000],
            ]
        )
    feedback.append("只输出符合 schema 的工具参数，不要解释。")
    return [
        *messages,
        UserMsg(name="user", content="\n".join(feedback)),
    ]


def _summarize_structured_response(response: object) -> str:
    content = getattr(response, "content", None)
    if content is None and hasattr(response, "get"):
        content = response.get("content")
    for block in content or []:
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        name = getattr(block, "name", None) or (
            block.get("name") if isinstance(block, dict) else None
        )
        if block_type != "tool_call" or name != _STRUCTURED_FUNC_NAME:
            continue
        raw = getattr(block, "input", None)
        if raw is None and isinstance(block, dict):
            raw = block.get("input")
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
            return json.dumps(raw, ensure_ascii=False, default=str)
    return _extract_agent_scope_text(response)


def _schema_of(structured_model: type[BaseModel] | dict[str, Any]) -> dict[str, Any]:
    if isinstance(structured_model, dict):
        return structured_model
    return structured_model.model_json_schema()


def _field_names(structured_model: type[BaseModel] | dict[str, Any]) -> set[str]:
    if isinstance(structured_model, dict):
        return set((structured_model.get("properties") or {}).keys())
    return set(structured_model.model_fields)


def _unwrap_single_key(
    data: dict[str, Any],
    structured_model: type[BaseModel] | dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(data, dict) or len(data) != 1:
        return data
    (only_key, inner), = data.items()
    if isinstance(inner, dict) and only_key not in _field_names(structured_model):
        return inner
    return data


def _extract_tool_args(response: object, schema: dict[str, Any]) -> dict[str, Any] | None:
    content = getattr(response, "content", None)
    if content is None and hasattr(response, "get"):
        content = response.get("content")
    for block in content or []:
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        name = getattr(block, "name", None) or (
            block.get("name") if isinstance(block, dict) else None
        )
        if block_type != "tool_call" or name != _STRUCTURED_FUNC_NAME:
            continue
        raw = getattr(block, "input", None)
        if raw is None and isinstance(block, dict):
            raw = block.get("input")
        if isinstance(raw, str):
            return _json_loads_with_repair(raw, schema)
        if isinstance(raw, dict):
            return raw
    return None


def _validate_structured_output(
    data: dict[str, Any],
    structured_model: type[BaseModel] | dict[str, Any],
) -> None:
    if isinstance(structured_model, dict):
        jsonschema.validate(data, structured_model)
    else:
        structured_model.model_validate(data)


def _enrich_sections_with_image_descriptions(
    sections: list[SourceSection],
    *,
    vision_agent: _VisionAgent | None,
    trace: TraceSink | None = None,
) -> list[SourceSection]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            _enrich_sections_with_image_descriptions_async(
                sections,
                vision_agent=vision_agent,
                trace=trace,
            )
        )
    raise RuntimeError("请在已运行的 event loop 内调用异步图片解析流程")


async def _enrich_sections_with_image_descriptions_async(
    sections: list[SourceSection],
    *,
    vision_agent: _VisionAgent | None,
    trace: TraceSink | None = None,
) -> list[SourceSection]:
    if vision_agent is None:
        return sections

    enriched_sections: list[SourceSection] = []
    for section in sections:
        enriched_sources: list[ParsedSourceFile] = []
        for source in section.sources:
            descriptions: list[str] = []
            for image in source.images:
                try:
                    description = await _describe_image_with_agent(
                        vision_agent,
                        image,
                        case_name=section.case_name,
                        section_name=section.section_name,
                        source=source,
                    )
                except Exception as exc:  # noqa: BLE001 - keep text source usable
                    emit_trace(
                        trace,
                        "generate.image_description_failed",
                        {
                            "section_name": section.section_name,
                            "filename": source.filename,
                            "image_filename": image.filename,
                            "error": repr(exc),
                        },
                    )
                    continue
                if not description.strip():
                    continue
                descriptions.append(
                    f"- 图片：{image.filename}\n"
                    f"  位置：{json.dumps(image.locator, ensure_ascii=False)}\n"
                    f"  描述：{description.strip()}"
                )
                emit_trace(
                    trace,
                    "generate.image_description_extracted",
                    {
                        "section_name": section.section_name,
                        "filename": source.filename,
                        "image_filename": image.filename,
                    },
                )
            if descriptions:
                enriched_sources.append(
                    replace(
                        source,
                        text=_append_image_descriptions(source.text, descriptions),
                    )
                )
            else:
                enriched_sources.append(source)
        enriched_sections.append(replace(section, sources=tuple(enriched_sources)))
    return enriched_sections


async def _describe_image_with_agent(
    vision_agent: _VisionAgent,
    image: ParsedEmbeddedImage,
    *,
    case_name: str,
    section_name: str,
    source: ParsedSourceFile,
) -> str:
    async_method = getattr(vision_agent, "describe_async", None)
    if async_method is not None:
        return await _maybe_await(
            async_method(
                image,
                case_name=case_name,
                section_name=section_name,
                source=source,
            )
        )
    return await _maybe_await(
        vision_agent.describe(
            image,
            case_name=case_name,
            section_name=section_name,
            source=source,
        )
    )


def _append_image_descriptions(text: str, descriptions: list[str]) -> str:
    block = "===== 图片补充信息（由多模态模型识别） =====\n" + "\n\n".join(
        descriptions
    )
    if text.strip():
        return text.rstrip() + "\n\n" + block
    return block


def _render_section_material(
    section: SourceSection,
    *,
    max_chars: int = DEFAULT_SECTION_MATERIAL_MAX_CHARS,
) -> str:
    blocks = [
        f"案例：{section.case_name}",
        f"章节：{section.section_name}",
        "请基于以下原始素材采集销售证据。",
    ]
    for source in section.sources:
        if source.is_empty:
            continue
        header = (
            "===== 来源："
            f"{source.source_type} ｜ 文件：{source.filename} "
            f"｜ source_id：{source.source_id} ====="
        )
        text = source.text
        used = len("\n\n".join(blocks))
        remaining = max_chars - used - len(header) - 4
        marker = "\n[内容已截断]"
        if remaining <= len(marker):
            blocks.append("[后续素材因上下文长度限制省略]")
            break
        if len(text) > remaining:
            text = text[: max(0, remaining - len(marker))].rstrip() + marker
        blocks.append(
            "\n".join(
                [
                    header,
                    text,
                ]
            )
        )
        if len("\n\n".join(blocks)) >= max_chars:
            break
    return "\n\n".join(blocks)


def _render_case_sales_evidence(
    case_name: str,
    evidences: list[SectionSalesEvidence],
) -> str:
    blocks = [f"案例：{case_name}", "以下是该案例下各章节的销售证据："]
    for evidence in evidences:
        blocks.append(
            "===== 章节："
            f"{evidence.section_name} =====\n"
            f"{json.dumps(evidence.model_dump(mode='json'), ensure_ascii=False, indent=2)}"
        )
    return "\n\n".join(blocks)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _run_section_generation_task(
    index: int,
    section: SourceSection,
    section_agent: _SectionAgent,
    *,
    output_dir: Path,
    trace: TraceSink | None,
    semaphore: asyncio.Semaphore,
) -> _SectionTaskSuccess | _SectionTaskFailure:
    async with semaphore:
        try:
            evidence = await _extract_section_by_source_async(
                section,
                section_agent,
                trace=trace,
            )
            if not _has_sales_evidence(evidence):
                raise SalesInsightGenerationError("章节未抽取到可用销售证据")
            path = _write_section_sales_evidence(evidence, output_dir)
            emit_trace(
                trace,
                "generate.section_evidence_written",
                {"section_name": section.section_name, "path": str(path)},
            )
            return _SectionTaskSuccess(index=index, evidence=evidence, path=path)
        except Exception as exc:  # noqa: BLE001 - keep other sections usable
            path = _write_section_sales_failure(section, output_dir, exc)
            emit_trace(
                trace,
                "generate.section_evidence_failed",
                {
                    "section_name": section.section_name,
                    "path": str(path),
                    "error": repr(exc),
                },
            )
            return _SectionTaskFailure(
                index=index,
                section_name=section.section_name,
                path=path,
                error=repr(exc),
            )


async def _extract_section_by_source_async(
    section: SourceSection,
    section_agent: _SectionAgent,
    *,
    trace: TraceSink | None = None,
) -> SectionSalesEvidence:
    source_evidences: list[SectionSalesEvidence] = []
    source_errors: list[str] = []
    source_exceptions: list[Exception] = []
    for source in section.sources:
        if source.is_empty:
            continue
        source_section = SourceSection(
            case_name=section.case_name,
            section_name=section.section_name,
            section_dir=section.section_dir,
            sources=(source,),
            skipped_files=section.skipped_files,
        )
        try:
            raw_evidence = await _extract_section_with_agent(
                section_agent,
                source_section,
            )
        except Exception as exc:  # noqa: BLE001 - keep other sources usable
            source_errors.append(f"{source.filename}: {repr(exc)}")
            source_exceptions.append(exc)
            emit_trace(
                trace,
                "generate.source_evidence_failed",
                {
                    "section_name": section.section_name,
                    "filename": source.filename,
                    "error": repr(exc),
                },
            )
            continue
        evidence = SectionSalesEvidence.model_validate(raw_evidence)
        evidence = evidence.model_copy(
            update={
                "case_name": evidence.case_name or section.case_name,
                "section_name": evidence.section_name or section.section_name,
            }
        )
        evidence = _enrich_section_evidence(evidence, (source,))
        if _has_sales_evidence(evidence):
            source_evidences.append(evidence)
            emit_trace(
                trace,
                "generate.source_evidence_extracted",
                {
                    "section_name": section.section_name,
                    "filename": source.filename,
                },
            )
    if not source_evidences and source_errors:
        if len(source_exceptions) == 1:
            raise source_exceptions[0]
        raise SalesInsightGenerationError(
            "章节所有来源抽取失败: " + "; ".join(source_errors)
        )
    return _merge_section_evidences(section, source_evidences)


async def _extract_section_with_agent(
    section_agent: _SectionAgent,
    section: SourceSection,
) -> SectionSalesEvidence:
    extract_section_async = getattr(section_agent, "extract_section_async", None)
    if extract_section_async is not None:
        return await _maybe_await(extract_section_async(section))
    extract_async = getattr(section_agent, "extract_async", None)
    if extract_async is not None:
        return await _maybe_await(extract_async(section))
    return await _maybe_await(section_agent.extract(section))


async def _extract_case_with_agent(
    case_agent: _CaseAgent,
    case_name: str,
    evidences: list[SectionSalesEvidence],
) -> CaseSalesInsightsSource:
    extract_case_async = getattr(case_agent, "extract_case_async", None)
    if extract_case_async is not None:
        return await _maybe_await(extract_case_async(case_name, evidences))
    extract_async = getattr(case_agent, "extract_async", None)
    if extract_async is not None:
        return await _maybe_await(extract_async(case_name, evidences))
    return await _maybe_await(case_agent.extract(case_name, evidences))


def _extract_section_by_source(
    section: SourceSection,
    section_agent: _SectionAgent,
    *,
    trace: TraceSink | None = None,
) -> SectionSalesEvidence:
    source_evidences: list[SectionSalesEvidence] = []
    for source in section.sources:
        if source.is_empty:
            continue
        source_section = SourceSection(
            case_name=section.case_name,
            section_name=section.section_name,
            section_dir=section.section_dir,
            sources=(source,),
            skipped_files=section.skipped_files,
        )
        try:
            raw_evidence = section_agent.extract(source_section)
        except Exception as exc:  # noqa: BLE001 - keep other sources usable
            emit_trace(
                trace,
                "generate.source_evidence_failed",
                {
                    "section_name": section.section_name,
                    "filename": source.filename,
                    "error": repr(exc),
                },
            )
            continue
        evidence = SectionSalesEvidence.model_validate(raw_evidence)
        evidence = evidence.model_copy(
            update={
                "case_name": evidence.case_name or section.case_name,
                "section_name": evidence.section_name or section.section_name,
            }
        )
        evidence = _enrich_section_evidence(evidence, (source,))
        if _has_sales_evidence(evidence):
            source_evidences.append(evidence)
            emit_trace(
                trace,
                "generate.source_evidence_extracted",
                {
                    "section_name": section.section_name,
                    "filename": source.filename,
                },
            )
    return _merge_section_evidences(section, source_evidences)


def _merge_section_evidences(
    section: SourceSection,
    evidences: list[SectionSalesEvidence],
) -> SectionSalesEvidence:
    return SectionSalesEvidence(
        case_name=section.case_name,
        section_name=section.section_name,
        customer_signals=[
            item for evidence in evidences for item in evidence.customer_signals
        ],
        sales_actions=[item for evidence in evidences for item in evidence.sales_actions],
        script_quotes=[item for evidence in evidences for item in evidence.script_quotes],
        objections=[item for evidence in evidences for item in evidence.objections],
        strategy_candidates=[
            item for evidence in evidences for item in evidence.strategy_candidates
        ],
    )


def _enrich_section_evidence(
    evidence: SectionSalesEvidence,
    sources: tuple[ParsedSourceFile, ...],
) -> SectionSalesEvidence:
    return evidence.model_copy(
        update={
            "customer_signals": [
                item.model_copy(
                    update={"source_refs": enrich_evidence_refs(item.source_refs, sources)}
                )
                for item in evidence.customer_signals
            ],
            "sales_actions": [
                item.model_copy(
                    update={"source_refs": enrich_evidence_refs(item.source_refs, sources)}
                )
                for item in evidence.sales_actions
            ],
            "script_quotes": [
                item.model_copy(
                    update={"source_refs": enrich_evidence_refs(item.source_refs, sources)}
                )
                for item in evidence.script_quotes
            ],
            "objections": [
                item.model_copy(
                    update={"source_refs": enrich_evidence_refs(item.source_refs, sources)}
                )
                for item in evidence.objections
            ],
            "strategy_candidates": [
                item.model_copy(
                    update={"source_refs": enrich_evidence_refs(item.source_refs, sources)}
                )
                for item in evidence.strategy_candidates
            ],
        }
    )


def _enrich_case_insights(
    insights: CaseSalesInsightsSource,
    sources: list[ParsedSourceFile],
) -> CaseSalesInsightsSource:
    return insights.model_copy(
        update={
            "customer_journey": [
                item.model_copy(
                    update={
                        "evidence_refs": enrich_evidence_refs(item.evidence_refs, sources)
                    }
                )
                for item in insights.customer_journey
            ],
            "strategies": [
                item.model_copy(
                    update={
                        "evidence_refs": enrich_evidence_refs(item.evidence_refs, sources)
                    }
                )
                for item in insights.strategies
            ],
            "scripts": [
                item.model_copy(
                    update={
                        "evidence_refs": enrich_evidence_refs(item.evidence_refs, sources)
                    }
                )
                for item in insights.scripts
            ],
            "objection_handling": [
                item.model_copy(
                    update={
                        "evidence_refs": enrich_evidence_refs(item.evidence_refs, sources)
                    }
                )
                for item in insights.objection_handling
            ],
        }
    )


def _has_sales_evidence(evidence: SectionSalesEvidence) -> bool:
    return bool(
        evidence.customer_signals
        or evidence.sales_actions
        or evidence.script_quotes
        or evidence.objections
        or evidence.strategy_candidates
    )


@dataclass(frozen=True)
class _CaseSalesWriteResult:
    insights_path: Path
    playbook_path: Path


def _write_section_sales_evidence(
    evidence: SectionSalesEvidence,
    output_dir: Path,
) -> Path:
    case_dir = output_dir / _safe_name(evidence.case_name)
    case_dir.mkdir(parents=True, exist_ok=True)
    path = case_dir / f"{_safe_name(evidence.section_name)}.sales_evidence.json"
    _atomic_write_text(
        path,
        json.dumps(evidence.model_dump(mode="json"), ensure_ascii=False, indent=2),
    )
    return path


def _write_section_sales_failure(
    section: SourceSection,
    output_dir: Path,
    exc: Exception,
) -> Path:
    case_dir = output_dir / _safe_name(section.case_name)
    case_dir.mkdir(parents=True, exist_ok=True)
    path = case_dir / f"{_safe_name(section.section_name)}.sales_evidence.failed.json"
    payload = {
        "status": "failed",
        "case_name": section.case_name,
        "section_name": section.section_name,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "repr": repr(exc),
        "attempts": getattr(exc, "attempts", 1),
        "sources": [
            {
                "source_id": source.source_id,
                "source_type": source.source_type,
                "filename": source.filename,
                "source_path": source.source_path,
            }
            for source in section.sources
        ],
    }
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def _write_case_sales_insights(
    insights: CaseSalesInsightsSource,
    output_dir: Path,
) -> _CaseSalesWriteResult:
    case_dir = output_dir / _safe_name(insights.case_name)
    case_dir.mkdir(parents=True, exist_ok=True)
    insights_path = case_dir / "case.sales_insights.json"
    playbook_path = case_dir / "case.sales_playbook.md"
    _atomic_write_text(
        insights_path,
        json.dumps(insights.model_dump(mode="json"), ensure_ascii=False, indent=2),
    )
    _atomic_write_text(playbook_path, _render_case_playbook(insights))
    return _CaseSalesWriteResult(
        insights_path=insights_path,
        playbook_path=playbook_path,
    )


def _render_case_playbook(insights: CaseSalesInsightsSource) -> str:
    lines = [
        f"# {insights.case_name} - 销售洞察手册",
        "",
        "## 案例概览",
        insights.case_summary or "无",
        "",
        "## 客户旅程",
    ]
    if insights.customer_journey:
        for step in insights.customer_journey:
            lines.extend(
                [
                    f"### {step.stage}",
                    f"- 客户状态: {step.customer_state}",
                    f"- 销售目标: {step.sales_goal}",
                    "- 关键动作:",
                    _render_list(step.key_actions),
                ]
            )
            lines.extend(_render_evidence_refs(step.evidence_refs))
            lines.append("")
    else:
        lines.extend(["无", ""])

    lines.append("## 销售策略")
    if insights.strategies:
        for strategy in insights.strategies:
            lines.extend(
                [
                    f"### {strategy.name}",
                    f"- 定义: {strategy.definition}",
                    f"- 适用阶段: {'、'.join(strategy.applicable_stages) or '未标注'}",
                    f"- 置信度: {strategy.confidence}",
                    f"- 模型归纳: {'是' if strategy.inferred else '否'}",
                    "- 步骤:",
                    _render_list(strategy.steps),
                    "- 建议做法:",
                    _render_list(strategy.do),
                    "- 避免做法:",
                    _render_list(strategy.dont),
                ]
            )
            lines.extend(_render_evidence_refs(strategy.evidence_refs))
            lines.append("")
    else:
        lines.extend(["无", ""])

    lines.append("## 场景话术")
    if insights.scripts:
        for script in insights.scripts:
            lines.extend(
                [
                    f"### {script.script_id} - {script.scenario}",
                    f"- 阶段: {script.stage}",
                    f"- 客户触发点: {script.customer_trigger}",
                    f"- 目标: {script.goal}",
                    f"- 原始话术: {script.source_quote}",
                    f"- 教练推荐话术: {script.coach_wording}",
                    f"- 关联策略: {'、'.join(script.strategy_names) or '未标注'}",
                    "- 追问建议:",
                    _render_list(script.follow_up_questions),
                    "- 合规提醒:",
                    _render_list(script.compliance_notes),
                ]
            )
            lines.extend(_render_evidence_refs(script.evidence_refs))
            lines.append("")
    else:
        lines.extend(["无", ""])

    lines.append("## 异议处理")
    if insights.objection_handling:
        for item in insights.objection_handling:
            lines.extend(
                [
                    f"### {item.objection}",
                    f"- 异议诊断: {item.diagnosis}",
                    f"- 推荐回应: {item.recommended_response}",
                    f"- 关联策略: {'、'.join(item.related_strategy_names) or '未标注'}",
                    f"- 关联话术: {'、'.join(item.related_script_ids) or '未标注'}",
                ]
            )
            lines.extend(_render_evidence_refs(item.evidence_refs))
            lines.append("")
    else:
        lines.extend(["无", ""])
    return "\n".join(lines).rstrip() + "\n"


def _render_list(items: list[str]) -> str:
    if not items:
        return "- 无"
    return "\n".join(f"- {item}" for item in items)


def _render_evidence_refs(refs: list[EvidenceRef]) -> list[str]:
    if not refs:
        return []
    lines = ["- 来源依据:"]
    for ref in refs:
        section = f"{ref.section_name} / " if ref.section_name else ""
        quote = f"：{ref.quote}" if ref.quote else ""
        lines.append(f"  - {section}{_format_ref_location(ref)}{quote}")
    return lines


def _format_ref_location(ref: EvidenceRef) -> str:
    filename = ref.filename or ref.source_id or "未标注来源"
    locator = ref.locator or {}
    parts = [filename]
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
    return " ".join(parts)


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value).strip("._ ")
    return safe or "case"


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")
    tmp.replace(path)


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SalesInsightGenerationError(f"模型响应不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SalesInsightGenerationError("模型响应顶层必须是 JSON object")
    return data


def _extract_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not choices:
        raise SalesInsightGenerationError("chat/completions 响应缺少 choices")
    message = choices[0].get("message", {})
    content = message.get("content")
    if not isinstance(content, str):
        raise SalesInsightGenerationError("chat/completions 响应缺少 message.content")
    return content


def _fill_blank_identity(data: dict[str, Any], field: str, fallback: str) -> dict[str, Any]:
    value = data.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        data[field] = fallback
    return data


_SECTION_SYSTEM_PROMPT = """你是保险公司 AI 教练系统的销售证据采集专家。

你的任务：从给定的单节绩优案例素材中，采集对销售策略和销售话术提取有价值的证据。

输出 JSON object，字段必须符合：
- case_name
- section_name
- customer_signals: [{signal, evidence, source_refs}]
- sales_actions: [{action, stage_hint, evidence, source_refs}]
- script_quotes: [{quote, speaker, stage_hint, scenario_hint, source_refs}]
- objections: [{objection, response_evidence, source_refs}]
- strategy_candidates: [{name, reason, confidence, inferred, source_refs}]

要求：
1. 严格基于素材，不得杜撰客户背景、产品条款、收益、理赔或监管要求。
2. 每条 source_refs 至少保留 filename 和 quote；如果素材里能判断 section_name/source_id，也要保留。
3. source_refs 的 quote 必须尽量使用素材原文短片段，便于系统回查行号/页码。
4. evidence、response_evidence、reason 需要写成 1-3 句完整证据说明，包含客户场景、销售动作或可复用点，不要只写标签。
5. 只做证据采集，不要把单节内容包装成完整方法论。
6. 不提取寒暄、致谢、无信息量口号和个人情绪抒发。"""


_CASE_SYSTEM_PROMPT = """你是保险公司 AI 教练系统的案例级销售洞察专家。

你会收到同一个绩优案例下多个章节的销售证据。你的任务是从完整案例视角提炼：
1. customer_journey：客户从售前到成交/经营的状态变化、销售目标和关键动作；
2. strategies：贯穿案例的销售策略，必须能被多个或明确的证据支持；
3. scripts：可复用的场景化话术，区分原始话术 source_quote 和教练推荐话术 coach_wording；
4. objection_handling：客户异议、异议诊断、推荐回应方式和关联话术。

输出 JSON object，字段必须符合：
- case_name
- case_summary
- customer_journey
- strategies
- scripts
- objection_handling

要求：
1. 以完整案例为单位归纳，不要把每节割裂成孤立结论。
2. 策略是对证据的抽象，不能把没有证据的通用销售理论塞进结果。
3. customer_journey、strategies、scripts、objection_handling 中每一项都必须保留 evidence_refs。
4. evidence_refs 必须从输入证据的 source_refs 继承，保留 filename、quote、context、
   source_id、source_type、source_path、locator、locator_confidence、anchor_id 等字段。
5. coach_wording 可以更适合教练训练，但必须忠于 source_quote 的语义。
6. 涉及收益、理赔、核保、产品责任、竞品比较时必须写合规提醒。
7. 如果某个策略只是模型归纳，请保留 inferred=true，不要包装成公司标准打法。"""
