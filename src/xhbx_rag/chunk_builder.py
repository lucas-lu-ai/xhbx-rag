from __future__ import annotations

from collections.abc import Iterable

from .models import (
    CaseSalesScript,
    CaseSalesStrategy,
    CustomerJourneyStep,
    EvidenceRef,
    ObjectionHandling,
    RagChunk,
    StructuredCaseKnowledge,
)
from .normalizer import make_case_id
from .tagging import tag_chunk


def _lines(items: Iterable[str]) -> list[str]:
    return [item for item in items if item]


def _bullet_block(title: str, values: list[str]) -> list[str]:
    if not values:
        return []
    return [f"{title}：", *(f"- {value}" for value in values)]


def _chunk_id(case_id: str, chunk_type: str, value: str, index: int) -> str:
    identity = value or str(index)
    return f"{case_id}__{chunk_type}__{make_case_id(identity)}"


def _citations(refs: list[EvidenceRef]) -> list[EvidenceRef]:
    return refs


def _evidence_block(refs: list[EvidenceRef]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        text = ref.source_excerpt.strip() or ref.context.strip() or ref.quote.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        source = ref.filename or ref.source_id
        values.append(f"{source}：{text}" if source else text)
    return _bullet_block("来源原文", values)


def _journey_chunk(
    case_id: str,
    case_name: str,
    item: CustomerJourneyStep,
    index: int,
) -> RagChunk:
    text = "\n".join(
        _lines(
            [
                f"案例：{case_name}",
                "知识类型：客户旅程",
                f"阶段：{item.stage}",
                f"客户状态：{item.customer_state}",
                f"销售目标：{item.sales_goal}",
                *_bullet_block("关键动作", item.key_actions),
                *_evidence_block(item.evidence_refs),
            ]
        )
    )
    return RagChunk(
        chunk_id=_chunk_id(case_id, "customer_journey", item.stage, index),
        chunk_type="customer_journey",
        text=text,
        metadata={
            "case_name": case_name,
            "stage": item.stage,
            "customer_state": item.customer_state,
        },
        citations=_citations(item.evidence_refs),
        source_file="case.sales_insights.json",
    )


def _strategy_chunk(
    case_id: str,
    case_name: str,
    item: CaseSalesStrategy,
    index: int,
) -> RagChunk:
    text = "\n".join(
        _lines(
            [
                f"案例：{case_name}",
                "知识类型：销售策略",
                f"策略名称：{item.name}",
                f"别名：{'、'.join(item.aliases)}" if item.aliases else "",
                f"定义：{item.definition}",
                f"适用阶段：{'、'.join(item.applicable_stages)}"
                if item.applicable_stages
                else "",
                *_bullet_block("步骤", item.steps),
                *_bullet_block("建议做法", item.do),
                *_bullet_block("避免做法", item.dont),
                f"置信度：{item.confidence}",
                f"模型归纳：{'是' if item.inferred else '否'}",
                *_evidence_block(item.evidence_refs),
            ]
        )
    )
    return RagChunk(
        chunk_id=_chunk_id(case_id, "strategy", item.name, index),
        chunk_type="strategy",
        text=text,
        metadata={
            "case_name": case_name,
            "strategy_name": item.name,
            "aliases": item.aliases,
            "applicable_stages": item.applicable_stages,
            "confidence": item.confidence,
            "inferred": item.inferred,
        },
        citations=_citations(item.evidence_refs),
        source_file="case.sales_insights.json",
    )


def _script_chunk(
    case_id: str,
    case_name: str,
    item: CaseSalesScript,
    index: int,
) -> RagChunk:
    text = "\n".join(
        _lines(
            [
                f"案例：{case_name}",
                "知识类型：场景话术",
                f"话术 ID：{item.script_id}",
                f"阶段：{item.stage}",
                f"场景：{item.scenario}",
                f"客户触发点：{item.customer_trigger}",
                f"目标：{item.goal}",
                f"原始话术：{item.source_quote}",
                f"教练推荐话术：{item.coach_wording}",
                f"关联策略：{'、'.join(item.strategy_names)}"
                if item.strategy_names
                else "",
                *_bullet_block("追问建议", item.follow_up_questions),
                *_bullet_block("合规提醒", item.compliance_notes),
                *_evidence_block(item.evidence_refs),
            ]
        )
    )
    return RagChunk(
        chunk_id=_chunk_id(case_id, "script", item.script_id or item.scenario, index),
        chunk_type="script",
        text=text,
        metadata={
            "case_name": case_name,
            "script_id": item.script_id,
            "stage": item.stage,
            "scenario": item.scenario,
            "customer_trigger": item.customer_trigger,
            "strategy_names": item.strategy_names,
        },
        citations=_citations(item.evidence_refs),
        source_file="case.sales_insights.json",
    )


def _objection_chunk(
    case_id: str,
    case_name: str,
    item: ObjectionHandling,
    index: int,
) -> RagChunk:
    text = "\n".join(
        _lines(
            [
                f"案例：{case_name}",
                "知识类型：异议处理",
                f"客户异议：{item.objection}",
                f"异议诊断：{item.diagnosis}",
                f"推荐回应：{item.recommended_response}",
                f"关联策略：{'、'.join(item.related_strategy_names)}"
                if item.related_strategy_names
                else "",
                f"关联话术：{'、'.join(item.related_script_ids)}"
                if item.related_script_ids
                else "",
                *_evidence_block(item.evidence_refs),
            ]
        )
    )
    return RagChunk(
        chunk_id=_chunk_id(case_id, "objection_handling", item.objection, index),
        chunk_type="objection_handling",
        text=text,
        metadata={
            "case_name": case_name,
            "objection": item.objection,
            "related_strategy_names": item.related_strategy_names,
            "related_script_ids": item.related_script_ids,
        },
        citations=_citations(item.evidence_refs),
        source_file="case.sales_insights.json",
    )


def build_chunks(knowledge: StructuredCaseKnowledge) -> list[RagChunk]:
    chunks: list[RagChunk] = []
    for index, item in enumerate(knowledge.customer_journey, start=1):
        chunks.append(
            _journey_chunk(knowledge.case_id, knowledge.case_name, item, index)
        )
    for index, item in enumerate(knowledge.strategies, start=1):
        chunks.append(
            _strategy_chunk(knowledge.case_id, knowledge.case_name, item, index)
        )
    for index, item in enumerate(knowledge.scripts, start=1):
        chunks.append(_script_chunk(knowledge.case_id, knowledge.case_name, item, index))
    for index, item in enumerate(knowledge.objection_handling, start=1):
        chunks.append(
            _objection_chunk(knowledge.case_id, knowledge.case_name, item, index)
        )
    return [tag_chunk(chunk) for chunk in chunks]
