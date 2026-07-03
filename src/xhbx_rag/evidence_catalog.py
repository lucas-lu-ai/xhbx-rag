"""证据句柄目录：给章节证据条目分配短 ID，供案例级分型调用按 ID 引用。

只把知识主文本渲染给模型；EvidenceRef 定位元数据保留在本地映射表，
组装阶段按 evidence_ids 回挂，模型不抄写任何引用字段。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Iterable

from .models import EvidenceRef, SectionSalesEvidence

_KIND_LABELS: dict[str, str] = {
    "customer_signal": "客户信号",
    "sales_action": "销售动作",
    "script_quote": "话术引用",
    "objection": "异议应对",
    "strategy_candidate": "策略候选",
}

_WHITESPACE_RE = re.compile(r"\s+")
_ID_TRIM_RE = re.compile(r"^[^0-9A-Za-z]+|[^0-9A-Za-z]+$")


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub("", text.strip())


def _single_line(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text.strip())


def _normalize_evidence_id(evidence_id: object) -> str:
    return _ID_TRIM_RE.sub("", str(evidence_id).strip()).upper()


def _ref_identity(ref: EvidenceRef) -> tuple[str, str, str]:
    return (ref.anchor_id, ref.quote, ref.source_id)


@dataclass(frozen=True)
class EvidenceCatalogEntry:
    evidence_id: str
    kind: str
    section_name: str
    summary: str
    dedup_key: str
    refs: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class EvidenceCatalog:
    case_name: str
    entries: tuple[EvidenceCatalogEntry, ...]

    def resolve_refs(
        self,
        evidence_ids: Iterable[str],
    ) -> tuple[list[EvidenceRef], list[str]]:
        by_id = {entry.evidence_id: entry for entry in self.entries}
        refs: list[EvidenceRef] = []
        seen: set[tuple[str, str, str]] = set()
        unknown: list[str] = []
        for evidence_id in evidence_ids:
            entry = by_id.get(_normalize_evidence_id(evidence_id))
            if entry is None:
                if evidence_id not in unknown:
                    unknown.append(evidence_id)
                continue
            for ref in entry.refs:
                identity = _ref_identity(ref)
                if identity in seen:
                    continue
                seen.add(identity)
                refs.append(ref)
        return refs, unknown

    def render_text(self) -> str:
        lines = [
            f"案例：{self.case_name}",
            "证据目录（每条以 [ID] 开头；引用证据时只写 ID，不要重复原文）：",
        ]
        current_section: str | None = None
        current_kind: str | None = None
        for entry in self.entries:
            if entry.section_name != current_section:
                current_section = entry.section_name
                current_kind = None
                lines.append("")
                lines.append(f"【章节：{current_section}】")
            if entry.kind != current_kind:
                current_kind = entry.kind
                lines.append(f"◇ {_KIND_LABELS.get(entry.kind, entry.kind)}")
            lines.append(f"[{entry.evidence_id}] {entry.summary}")
        return "\n".join(lines)


@dataclass(frozen=True)
class _PendingEntry:
    kind: str
    section_name: str
    summary: str
    dedup_key: str
    refs: tuple[EvidenceRef, ...]


def _merge_refs(
    existing: tuple[EvidenceRef, ...],
    incoming: tuple[EvidenceRef, ...],
) -> tuple[EvidenceRef, ...]:
    merged = list(existing)
    seen = {_ref_identity(ref) for ref in existing}
    for ref in incoming:
        identity = _ref_identity(ref)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(ref)
    return tuple(merged)


def _signal_pending(section: SectionSalesEvidence) -> list[_PendingEntry]:
    pendings: list[_PendingEntry] = []
    for item in section.customer_signals:
        summary = f"{item.signal}｜解释：{item.evidence}" if item.evidence else item.signal
        pendings.append(
            _PendingEntry(
                kind="customer_signal",
                section_name=section.section_name,
                summary=summary,
                dedup_key=_normalize(item.signal + item.evidence),
                refs=tuple(item.source_refs),
            )
        )
    return pendings


def _action_pending(section: SectionSalesEvidence) -> list[_PendingEntry]:
    pendings: list[_PendingEntry] = []
    for item in section.sales_actions:
        stage = f"[阶段：{item.stage_hint}] " if item.stage_hint else ""
        summary = f"{stage}{item.action}"
        if item.evidence:
            summary += f"｜解释：{item.evidence}"
        pendings.append(
            _PendingEntry(
                kind="sales_action",
                section_name=section.section_name,
                summary=summary,
                dedup_key=_normalize(item.action + item.evidence),
                refs=tuple(item.source_refs),
            )
        )
    return pendings


def _script_pending(section: SectionSalesEvidence) -> list[_PendingEntry]:
    pendings: list[_PendingEntry] = []
    for item in section.script_quotes:
        hints = "·".join(
            part
            for part in (item.speaker, item.stage_hint, item.scenario_hint)
            if part
        )
        summary = f"（{hints}）“{item.quote}”" if hints else f"“{item.quote}”"
        pendings.append(
            _PendingEntry(
                kind="script_quote",
                section_name=section.section_name,
                summary=summary,
                dedup_key=_normalize(item.quote),
                refs=tuple(item.source_refs),
            )
        )
    return pendings


def _objection_pending(section: SectionSalesEvidence) -> list[_PendingEntry]:
    pendings: list[_PendingEntry] = []
    for item in section.objections:
        summary = f"异议：{item.objection}"
        if item.response_evidence:
            summary += f"｜应对：{item.response_evidence}"
        pendings.append(
            _PendingEntry(
                kind="objection",
                section_name=section.section_name,
                summary=summary,
                dedup_key=_normalize(item.objection + item.response_evidence),
                refs=tuple(item.source_refs),
            )
        )
    return pendings


def _strategy_pending(section: SectionSalesEvidence) -> list[_PendingEntry]:
    pendings: list[_PendingEntry] = []
    for item in section.strategy_candidates:
        qualifiers = "·".join(
            part
            for part in (
                f"置信度 {item.confidence}" if item.confidence else "",
                "推断" if item.inferred else "",
            )
            if part
        )
        summary = f"{item.name}（{qualifiers}）" if qualifiers else item.name
        if item.reason:
            summary += f"｜理由：{item.reason}"
        pendings.append(
            _PendingEntry(
                kind="strategy_candidate",
                section_name=section.section_name,
                summary=summary,
                dedup_key=_normalize(item.name + item.reason),
                refs=tuple(item.source_refs),
            )
        )
    return pendings


def _pending_entries(section: SectionSalesEvidence) -> list[_PendingEntry]:
    return [
        *_signal_pending(section),
        *_action_pending(section),
        *_script_pending(section),
        *_objection_pending(section),
        *_strategy_pending(section),
    ]


def _anchor_keys(pending: _PendingEntry) -> list[tuple[str, str, str]]:
    return [
        (pending.kind, ref.anchor_id, ref.quote)
        for ref in pending.refs
        if ref.anchor_id and ref.quote
    ]


def build_evidence_catalog(
    case_name: str,
    evidences: list[SectionSalesEvidence],
) -> EvidenceCatalog:
    """构建句柄目录并做 L1 确定性去重。

    合并规则（同 kind 内）：主文本规范化后相同，或存在 anchor_id+quote
    完全相同的引用；合并时 citations 取并集、保留首次出现的主文本。
    """
    merged: list[_PendingEntry] = []
    by_dedup_key: dict[tuple[str, str], int] = {}
    by_anchor: dict[tuple[str, str, str], int] = {}
    for section in evidences:
        for pending in _pending_entries(section):
            text_key = (pending.kind, pending.dedup_key)
            match_index = by_dedup_key.get(text_key)
            if match_index is None:
                for anchor_key in _anchor_keys(pending):
                    if anchor_key in by_anchor:
                        match_index = by_anchor[anchor_key]
                        break
            if match_index is not None:
                existing = merged[match_index]
                merged[match_index] = replace(
                    existing,
                    refs=_merge_refs(existing.refs, pending.refs),
                )
            else:
                match_index = len(merged)
                merged.append(pending)
                by_dedup_key[text_key] = match_index
            for anchor_key in _anchor_keys(pending):
                by_anchor.setdefault(anchor_key, match_index)

    entries = tuple(
        EvidenceCatalogEntry(
            evidence_id=f"E{index + 1:03d}",
            kind=pending.kind,
            section_name=pending.section_name,
            summary=_single_line(pending.summary),
            dedup_key=pending.dedup_key,
            refs=pending.refs,
        )
        for index, pending in enumerate(merged)
    )
    return EvidenceCatalog(case_name=case_name, entries=entries)
