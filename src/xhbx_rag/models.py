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


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    section_name: str = ""
    source_id: str = ""
    filename: str = ""
    quote: str = ""


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
