from xhbx_rag.chunk_builder import build_chunks
from xhbx_rag.models import (
    CaseSalesScript,
    CaseSalesStrategy,
    EvidenceRef,
    ObjectionHandling,
    StructuredCaseKnowledge,
)


def _knowledge() -> StructuredCaseKnowledge:
    ref = EvidenceRef(
        section_name="第1节",
        filename="讲义.txt",
        quote="客户不想聊保险。",
        context="客户抗拒保险的场景摘要",
        source_excerpt=(
            "客户：我不想聊保险。\n销售：那我们先聊家庭责任和最近担心的风险。"
        ),
    )
    return StructuredCaseKnowledge(
        case_id="案例a_1234567890",
        case_name="案例A",
        case_summary="摘要",
        source_files=["case.sales_insights.json"],
        customer_journey=[],
        strategies=[
            CaseSalesStrategy(
                name="风险唤醒",
                definition="用家庭责任建立风险意识。",
                applicable_stages=["售前"],
                steps=["确认家庭责任", "追问保障缺口"],
                do=["先接纳客户顾虑"],
                dont=["不承诺收益"],
                confidence="high",
                evidence_refs=[ref],
            )
        ],
        scripts=[
            CaseSalesScript(
                script_id="script_001",
                stage="售前",
                scenario="客户抗拒保险",
                customer_trigger="客户不想聊保险",
                goal="打开话题",
                source_quote="客户不想聊保险。",
                coach_wording="先聊家庭责任。",
                strategy_names=["风险唤醒"],
                follow_up_questions=["现在家庭责任主要集中在哪里？"],
                compliance_notes=["不承诺收益"],
                evidence_refs=[ref],
            )
        ],
        objection_handling=[
            ObjectionHandling(
                objection="我不想聊保险",
                diagnosis="客户对保险话题有防御心理。",
                recommended_response="先询问家庭责任和当前担心。",
                related_strategy_names=["风险唤醒"],
                related_script_ids=["script_001"],
                evidence_refs=[ref],
            )
        ],
    )


def test_build_chunks_creates_one_chunk_per_object() -> None:
    chunks = build_chunks(_knowledge())

    assert [chunk.chunk_type for chunk in chunks] == [
        "strategy",
        "script",
        "objection_handling",
    ]


def test_script_chunk_contains_retrieval_text_metadata_and_citations() -> None:
    script_chunk = [
        chunk for chunk in build_chunks(_knowledge()) if chunk.chunk_type == "script"
    ][0]

    assert "场景：客户抗拒保险" in script_chunk.text
    assert "教练推荐话术：先聊家庭责任。" in script_chunk.text
    assert "来源原文：" in script_chunk.text
    assert "销售：那我们先聊家庭责任和最近担心的风险。" in script_chunk.text
    assert "客户抗拒保险的场景摘要" not in script_chunk.text
    assert script_chunk.metadata["stage"] == "售前"
    assert script_chunk.metadata["strategy_names"] == ["风险唤醒"]
    assert script_chunk.metadata["knowledge_type"] == "场景话术"
    assert "销售技能/沟通谈判/保险理念沟通" in script_chunk.metadata["tag_paths"]
    assert "客户需求/家庭责任" in script_chunk.metadata["tag_paths"]
    assert "标签：" in script_chunk.text
    assert "销售技能/沟通谈判/保险理念沟通" in script_chunk.text
    assert script_chunk.text.count("标签：") == 1
    assert script_chunk.citations[0].quote == "客户不想聊保险。"
