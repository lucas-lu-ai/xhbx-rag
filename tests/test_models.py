from xhbx_rag.models import CaseSalesInsightsSource, CaseSalesStrategy, EvidenceRef
from xhbx_rag.models import StrategyCandidate


def test_missing_top_level_lists_default_to_empty() -> None:
    source = CaseSalesInsightsSource.model_validate(
        {
            "case_name": "案例A",
            "case_summary": "案例摘要",
        }
    )

    assert source.customer_journey == []
    assert source.strategies == []
    assert source.scripts == []
    assert source.objection_handling == []


def test_evidence_ref_defaults_to_empty_strings() -> None:
    ref = EvidenceRef.model_validate({})

    assert ref.section_name == ""
    assert ref.source_id == ""
    assert ref.filename == ""
    assert ref.quote == ""
    assert ref.context == ""
    assert ref.source_excerpt == ""
    assert ref.locator_error == ""


def test_evidence_ref_preserves_source_locator_fields() -> None:
    ref = EvidenceRef.model_validate(
        {
            "section_name": "第1节",
            "source_id": "txt:第1节.track-0.txt",
            "filename": "第1节.track-0.txt",
            "source_type": "txt",
            "source_path": "案例A/第1节/第1节.track-0.txt",
            "quote": "客户说每年不能超过80万",
            "context": "老师开场\n客户说每年不能超过80万\n销售回应可以做预算释放",
            "source_excerpt": "客户说每年不能超过80万",
            "locator": {
                "line_start": 2,
                "line_end": 2,
                "char_start": 11,
                "char_end": 24,
            },
            "locator_confidence": "exact",
            "locator_error": "",
            "anchor_id": "txt:第1节.track-0.txt#line-2",
        }
    )

    dumped = ref.model_dump(mode="json")
    assert dumped["source_type"] == "txt"
    assert dumped["source_path"] == "案例A/第1节/第1节.track-0.txt"
    assert dumped["context"].startswith("老师开场")
    assert dumped["source_excerpt"] == "客户说每年不能超过80万"
    assert dumped["locator"]["line_start"] == 2
    assert dumped["locator_confidence"] == "exact"
    assert dumped["locator_error"] == ""
    assert dumped["anchor_id"] == "txt:第1节.track-0.txt#line-2"


def test_confidence_fields_accept_numeric_model_output() -> None:
    candidate = StrategyCandidate.model_validate(
        {"name": "预算释放", "reason": "证据明确", "confidence": 0.9}
    )
    strategy = CaseSalesStrategy.model_validate(
        {"name": "预算释放", "definition": "定义", "confidence": 0.5}
    )

    assert candidate.confidence == "high"
    assert strategy.confidence == "mid"


def test_case_part_draft_models_accept_evidence_ids_and_ignore_extras() -> None:
    from xhbx_rag.models import (
        CaseJourneyPart,
        CaseObjectionsPart,
        CaseScriptsPart,
        CaseStrategiesPart,
    )

    journey = CaseJourneyPart.model_validate(
        {
            "case_summary": "全案摘要",
            "customer_journey": [
                {
                    "stage": "需求挖掘",
                    "customer_state": "有预算顾虑",
                    "sales_goal": "确认预算红线",
                    "key_actions": "确认红线",
                    "evidence_ids": ["E001", "E002"],
                    "extra_field": "忽略",
                }
            ],
        }
    )
    strategies = CaseStrategiesPart.model_validate(
        {
            "strategies": [
                {
                    "name": "预算释放",
                    "definition": "用缴清保单释放预算",
                    "confidence": 0.9,
                    "evidence_ids": "E001",
                }
            ]
        }
    )
    scripts = CaseScriptsPart.model_validate(
        {
            "scripts": [
                {
                    "script_id": "script_001",
                    "source_quote": "客户说每年不能超过80万",
                    "evidence_ids": ["E003"],
                }
            ]
        }
    )
    objections = CaseObjectionsPart.model_validate(
        {
            "objection_handling": [
                {
                    "objection": "保险收益太低",
                    "diagnosis": "客户用投资收益率衡量保险",
                    "evidence_ids": ["E004"],
                }
            ]
        }
    )

    assert journey.case_summary == "全案摘要"
    assert journey.customer_journey[0].evidence_ids == ["E001", "E002"]
    assert journey.customer_journey[0].key_actions == ["确认红线"]
    assert strategies.strategies[0].confidence == "high"
    assert strategies.strategies[0].evidence_ids == ["E001"]
    assert scripts.scripts[0].script_id == "script_001"
    assert objections.objection_handling[0].diagnosis == "客户用投资收益率衡量保险"
