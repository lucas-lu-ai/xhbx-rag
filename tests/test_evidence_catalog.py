from xhbx_rag.evidence_catalog import build_evidence_catalog
from xhbx_rag.models import (
    CustomerSignal,
    EvidenceRef,
    ObjectionEvidence,
    SalesAction,
    ScriptQuote,
    SectionSalesEvidence,
    StrategyCandidate,
)


def _ref(anchor: str, quote: str, section: str = "第1节") -> EvidenceRef:
    return EvidenceRef(
        section_name=section,
        source_id="txt:a.txt",
        filename="a.txt",
        source_type="txt",
        quote=quote,
        source_excerpt=f"{quote}的原文行",
        locator={"line_start": 2, "line_end": 3},
        locator_confidence="validated_span",
        anchor_id=anchor,
    )


def _section_one() -> SectionSalesEvidence:
    return SectionSalesEvidence(
        case_name="案例A",
        section_name="第1节",
        customer_signals=[
            CustomerSignal(
                signal="客户对收益话题敏感度低",
                evidence="谈收益找不到突破口",
                source_refs=[_ref("txt:a.txt#line-2", "收益不高")],
            )
        ],
        sales_actions=[
            SalesAction(
                action="升级为财富管理顾问定位",
                stage_hint="需求挖掘",
                evidence="聚焦客户问题担心心愿",
                source_refs=[_ref("txt:a.txt#line-5", "私人财富管理")],
            )
        ],
        script_quotes=[
            ScriptQuote(
                quote="您的几个子女哪一个最像？",
                speaker="销售",
                stage_hint="需求挖掘",
                scenario_hint="生活话题转场",
                source_refs=[_ref("txt:a.txt#line-8", "子女哪一个最像")],
            )
        ],
        objections=[
            ObjectionEvidence(
                objection="保险收益太低",
                response_evidence="强调备用现金企业定位",
                source_refs=[_ref("txt:a.txt#line-11", "生产现金的备用企业")],
            )
        ],
        strategy_candidates=[
            StrategyCandidate(
                name="生活化提问诊断法",
                reason="降低客户戒备收集家庭信息",
                confidence="high",
                inferred=False,
                source_refs=[_ref("txt:a.txt#line-14", "生活的一个话题")],
            )
        ],
    )


def _section_two_with_duplicates() -> SectionSalesEvidence:
    return SectionSalesEvidence(
        case_name="案例A",
        section_name="第2节",
        script_quotes=[
            ScriptQuote(
                quote="您的几个子女哪一个最像？",
                speaker="销售",
                stage_hint="破冰",
                source_refs=[
                    _ref("pptx:b.pptx#line-3", "子女哪一个最像", section="第2节")
                ],
            )
        ],
        strategy_candidates=[
            StrategyCandidate(
                name="第三方权威背书策略",
                reason="用权威资料背书",
                confidence="high",
                source_refs=[
                    _ref("pptx:b.pptx#line-9", "第三方权威资料", section="第2节")
                ],
            ),
            StrategyCandidate(
                name="第三方权威资料借力策略",
                reason="借助权威资料让客户信服",
                confidence="mid",
                source_refs=[
                    _ref("pptx:b.pptx#line-9", "第三方权威资料", section="第2节")
                ],
            ),
        ],
    )


def test_catalog_assigns_deterministic_ids_across_all_kinds() -> None:
    catalog = build_evidence_catalog("案例A", [_section_one()])

    assert [entry.evidence_id for entry in catalog.entries] == [
        "E001",
        "E002",
        "E003",
        "E004",
        "E005",
    ]
    assert [entry.kind for entry in catalog.entries] == [
        "customer_signal",
        "sales_action",
        "script_quote",
        "objection",
        "strategy_candidate",
    ]
    rebuilt = build_evidence_catalog("案例A", [_section_one()])
    assert [e.evidence_id for e in rebuilt.entries] == [
        e.evidence_id for e in catalog.entries
    ]


def test_catalog_render_contains_knowledge_text_but_no_locator_metadata() -> None:
    catalog = build_evidence_catalog("案例A", [_section_one()])

    text = catalog.render_text()

    assert "案例A" in text
    assert "[E001]" in text
    assert "您的几个子女哪一个最像？" in text
    assert "生活化提问诊断法" in text
    for banned in ("line_start", "locator", "anchor_id", "source_excerpt", "txt:a.txt"):
        assert banned not in text


def test_catalog_merges_exact_duplicate_script_quotes_across_sections() -> None:
    catalog = build_evidence_catalog(
        "案例A", [_section_one(), _section_two_with_duplicates()]
    )

    quotes = [e for e in catalog.entries if e.kind == "script_quote"]
    assert len(quotes) == 1
    assert len(quotes[0].refs) == 2
    anchors = {ref.anchor_id for ref in quotes[0].refs}
    assert anchors == {"txt:a.txt#line-8", "pptx:b.pptx#line-3"}


def test_catalog_merges_entries_sharing_anchor_and_quote() -> None:
    catalog = build_evidence_catalog("案例A", [_section_two_with_duplicates()])

    strategies = [e for e in catalog.entries if e.kind == "strategy_candidate"]
    assert len(strategies) == 1
    assert "第三方权威背书策略" in strategies[0].summary


def test_resolve_refs_returns_refs_and_reports_unknown_ids() -> None:
    catalog = build_evidence_catalog("案例A", [_section_one()])

    refs, unknown = catalog.resolve_refs(["E001", "E999", "E001"])

    assert unknown == ["E999"]
    assert len(refs) == 1
    assert refs[0].anchor_id == "txt:a.txt#line-2"


def test_resolve_refs_tolerates_common_id_format_deviations() -> None:
    catalog = build_evidence_catalog("案例A", [_section_one()])

    refs, unknown = catalog.resolve_refs(["[E001]", " e002 ", "E003。"])

    assert unknown == []
    assert len(refs) == 3


def test_render_text_flattens_newlines_inside_summaries() -> None:
    section = SectionSalesEvidence(
        case_name="案例A",
        section_name="第1节",
        customer_signals=[
            CustomerSignal(
                signal="第一行\n第二行",
                evidence="解释也有\n换行",
                source_refs=[_ref("txt:a.txt#line-2", "收益不高")],
            )
        ],
    )

    text = build_evidence_catalog("案例A", [section]).render_text()

    entry_lines = [line for line in text.splitlines() if line.startswith("[E001]")]
    assert len(entry_lines) == 1
    assert "第一行 第二行" in entry_lines[0]
    assert "解释也有 换行" in entry_lines[0]
