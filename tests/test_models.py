from xhbx_rag.models import CaseSalesInsightsSource, EvidenceRef


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
