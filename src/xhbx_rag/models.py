from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _coerce_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _coerce_model_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return []


def _coerce_confidence_level(value: object) -> ConfidenceLevel:
    if isinstance(value, (int, float)):
        number = float(value)
        if number >= 0.75:
            return "high"
        if number >= 0.4:
            return "mid"
        return "low"
    text = str(value or "").strip().lower()
    if text in {"high", "高", "高置信", "高置信度"}:
        return "high"
    if text in {"mid", "medium", "middle", "中", "中等", "中置信度"}:
        return "mid"
    if text in {"low", "低", "低置信", "低置信度"}:
        return "low"
    return "low"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    section_name: str = ""
    source_id: str = ""
    filename: str = ""
    source_type: str = ""
    source_path: str = ""
    quote: str = ""
    context: str = ""
    source_excerpt: str = ""
    locator: dict[str, Any] = Field(default_factory=dict)
    locator_confidence: str = ""
    locator_error: str = ""
    anchor_id: str = ""


class CustomerSignal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    signal: str = ""
    evidence: str = ""
    source_refs: list[EvidenceRef] = Field(default_factory=list)

    @field_validator("source_refs", mode="before")
    @classmethod
    def _source_refs(cls, value: object) -> list[object]:
        return _coerce_model_list(value)


class SalesAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str = ""
    stage_hint: str = ""
    evidence: str = ""
    source_refs: list[EvidenceRef] = Field(default_factory=list)

    @field_validator("source_refs", mode="before")
    @classmethod
    def _source_refs(cls, value: object) -> list[object]:
        return _coerce_model_list(value)


class ScriptQuote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quote: str = ""
    speaker: str = ""
    stage_hint: str = ""
    scenario_hint: str = ""
    source_refs: list[EvidenceRef] = Field(default_factory=list)

    @field_validator("source_refs", mode="before")
    @classmethod
    def _source_refs(cls, value: object) -> list[object]:
        return _coerce_model_list(value)


class ObjectionEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    objection: str = ""
    response_evidence: str = ""
    source_refs: list[EvidenceRef] = Field(default_factory=list)

    @field_validator("source_refs", mode="before")
    @classmethod
    def _source_refs(cls, value: object) -> list[object]:
        return _coerce_model_list(value)


ConfidenceLevel = Literal["high", "mid", "low"]


class StrategyCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    reason: str = ""
    confidence: ConfidenceLevel = "low"
    inferred: bool = True
    source_refs: list[EvidenceRef] = Field(default_factory=list)

    @field_validator("source_refs", mode="before")
    @classmethod
    def _source_refs(cls, value: object) -> list[object]:
        return _coerce_model_list(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> ConfidenceLevel:
        return _coerce_confidence_level(value)


class SectionSalesEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_name: str = ""
    section_name: str = ""
    customer_signals: list[CustomerSignal] = Field(default_factory=list)
    sales_actions: list[SalesAction] = Field(default_factory=list)
    script_quotes: list[ScriptQuote] = Field(default_factory=list)
    objections: list[ObjectionEvidence] = Field(default_factory=list)
    strategy_candidates: list[StrategyCandidate] = Field(default_factory=list)

    @field_validator(
        "customer_signals",
        "sales_actions",
        "script_quotes",
        "objections",
        "strategy_candidates",
        mode="before",
    )
    @classmethod
    def _lists(cls, value: object) -> list[object]:
        return _coerce_model_list(value)


class CustomerJourneyStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stage: str = ""
    customer_state: str = ""
    sales_goal: str = ""
    key_actions: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    @field_validator("key_actions", mode="before")
    @classmethod
    def _key_actions(cls, value: object) -> list[str]:
        return _coerce_str_list(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: object) -> list[object]:
        return _coerce_model_list(value)


class CaseSalesStrategy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    aliases: list[str] = Field(default_factory=list)
    definition: str = ""
    applicable_stages: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    do: list[str] = Field(default_factory=list)
    dont: list[str] = Field(default_factory=list)
    confidence: str = ""
    inferred: bool = True
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    @field_validator(
        "aliases",
        "applicable_stages",
        "steps",
        "do",
        "dont",
        mode="before",
    )
    @classmethod
    def _str_lists(cls, value: object) -> list[str]:
        return _coerce_str_list(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: object) -> list[object]:
        return _coerce_model_list(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> ConfidenceLevel:
        return _coerce_confidence_level(value)


class CaseSalesScript(BaseModel):
    model_config = ConfigDict(extra="ignore")

    script_id: str = ""
    stage: str = ""
    scenario: str = ""
    customer_trigger: str = ""
    goal: str = ""
    source_quote: str = ""
    coach_wording: str = ""
    strategy_names: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    compliance_notes: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    @field_validator(
        "strategy_names",
        "follow_up_questions",
        "compliance_notes",
        mode="before",
    )
    @classmethod
    def _str_lists(cls, value: object) -> list[str]:
        return _coerce_str_list(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: object) -> list[object]:
        return _coerce_model_list(value)


class ObjectionHandling(BaseModel):
    model_config = ConfigDict(extra="ignore")

    objection: str = ""
    diagnosis: str = ""
    recommended_response: str = ""
    related_strategy_names: list[str] = Field(default_factory=list)
    related_script_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    @field_validator("related_strategy_names", "related_script_ids", mode="before")
    @classmethod
    def _str_lists(cls, value: object) -> list[str]:
        return _coerce_str_list(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: object) -> list[object]:
        return _coerce_model_list(value)


class CaseSalesInsightsSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_name: str
    case_summary: str = ""
    customer_journey: list[CustomerJourneyStep] = Field(default_factory=list)
    strategies: list[CaseSalesStrategy] = Field(default_factory=list)
    scripts: list[CaseSalesScript] = Field(default_factory=list)
    objection_handling: list[ObjectionHandling] = Field(default_factory=list)

    @field_validator(
        "customer_journey",
        "strategies",
        "scripts",
        "objection_handling",
        mode="before",
    )
    @classmethod
    def _lists(cls, value: object) -> list[object]:
        return _coerce_model_list(value)


class _EvidenceIdsMixin(BaseModel):
    """案例级分型调用的草稿模型基类：模型只引用证据短 ID，不抄写 EvidenceRef。"""

    model_config = ConfigDict(extra="ignore")

    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def _evidence_ids(cls, value: object) -> list[str]:
        return _coerce_str_list(value)


class CustomerJourneyStepDraft(_EvidenceIdsMixin):
    stage: str = ""
    customer_state: str = ""
    sales_goal: str = ""
    key_actions: list[str] = Field(default_factory=list)

    @field_validator("key_actions", mode="before")
    @classmethod
    def _key_actions(cls, value: object) -> list[str]:
        return _coerce_str_list(value)


class CaseSalesStrategyDraft(_EvidenceIdsMixin):
    name: str = ""
    aliases: list[str] = Field(default_factory=list)
    definition: str = ""
    applicable_stages: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    do: list[str] = Field(default_factory=list)
    dont: list[str] = Field(default_factory=list)
    confidence: str = ""
    inferred: bool = True

    @field_validator(
        "aliases",
        "applicable_stages",
        "steps",
        "do",
        "dont",
        mode="before",
    )
    @classmethod
    def _str_lists(cls, value: object) -> list[str]:
        return _coerce_str_list(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> ConfidenceLevel:
        return _coerce_confidence_level(value)


class CaseSalesScriptDraft(_EvidenceIdsMixin):
    script_id: str = ""
    stage: str = ""
    scenario: str = ""
    customer_trigger: str = ""
    goal: str = ""
    source_quote: str = ""
    coach_wording: str = ""
    strategy_names: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    compliance_notes: list[str] = Field(default_factory=list)

    @field_validator(
        "strategy_names",
        "follow_up_questions",
        "compliance_notes",
        mode="before",
    )
    @classmethod
    def _str_lists(cls, value: object) -> list[str]:
        return _coerce_str_list(value)


class ObjectionHandlingDraft(_EvidenceIdsMixin):
    objection: str = ""
    diagnosis: str = ""
    recommended_response: str = ""
    related_strategy_names: list[str] = Field(default_factory=list)
    related_script_ids: list[str] = Field(default_factory=list)

    @field_validator("related_strategy_names", "related_script_ids", mode="before")
    @classmethod
    def _str_lists(cls, value: object) -> list[str]:
        return _coerce_str_list(value)


class CaseJourneyPart(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_summary: str = ""
    customer_journey: list[CustomerJourneyStepDraft] = Field(default_factory=list)

    @field_validator("customer_journey", mode="before")
    @classmethod
    def _lists(cls, value: object) -> list[object]:
        return _coerce_model_list(value)


class CaseStrategiesPart(BaseModel):
    model_config = ConfigDict(extra="ignore")

    strategies: list[CaseSalesStrategyDraft] = Field(default_factory=list)

    @field_validator("strategies", mode="before")
    @classmethod
    def _lists(cls, value: object) -> list[object]:
        return _coerce_model_list(value)


class CaseScriptsPart(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scripts: list[CaseSalesScriptDraft] = Field(default_factory=list)

    @field_validator("scripts", mode="before")
    @classmethod
    def _lists(cls, value: object) -> list[object]:
        return _coerce_model_list(value)


class CaseObjectionsPart(BaseModel):
    model_config = ConfigDict(extra="ignore")

    objection_handling: list[ObjectionHandlingDraft] = Field(default_factory=list)

    @field_validator("objection_handling", mode="before")
    @classmethod
    def _lists(cls, value: object) -> list[object]:
        return _coerce_model_list(value)


class StructuredCaseKnowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    case_name: str
    case_summary: str
    source_files: list[str]
    customer_journey: list[CustomerJourneyStep]
    strategies: list[CaseSalesStrategy]
    scripts: list[CaseSalesScript]
    objection_handling: list[ObjectionHandling]


ChunkType = Literal[
    "customer_journey",
    "strategy",
    "script",
    "objection_handling",
    "training_course",
]


class RagChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    chunk_type: ChunkType
    text: str
    metadata: dict[str, Any]
    citations: list[EvidenceRef]
    source_file: str


class ParseReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_files: dict[str, str | None]
    output_files: dict[str, str]
    case_name: str | None
    counts: dict[str, int]
    warnings: list[str]
    errors: list[str]
