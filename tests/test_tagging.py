from xhbx_rag.models import EvidenceRef, RagChunk
from xhbx_rag.tagging import render_tag_line, tag_chunk


def test_tag_chunk_adds_chinese_tags_from_text_and_metadata() -> None:
    chunk = RagChunk(
        chunk_id="chunk-1",
        chunk_type="script",
        text="场景：高净值客户保险需求诊断\n客户触发点：客户关注财富传承和资产隔离",
        metadata={
            "case_name": "案例A",
            "stage": "需求分析",
            "scenario": "高净值客户保险需求诊断",
        },
        citations=[
            EvidenceRef(
                quote="客户关注财富传承",
                context="企业资产和家庭资产隔离",
            )
        ],
        source_file="case.sales_insights.json",
    )

    tagged = tag_chunk(chunk)

    assert tagged.metadata["knowledge_type"] == "场景话术"
    assert "客户经营/特殊客户服务/高净值客户服务" in tagged.metadata["tag_paths"]
    assert "客户画像/高净值客户" in tagged.metadata["tag_paths"]
    assert "客户需求/财富传承" in tagged.metadata["tag_paths"]
    assert "客户需求/资产隔离" in tagged.metadata["tag_paths"]
    assert tagged.metadata["sales_stages"] == ["需求分析"]
    assert tagged.metadata["customer_segments"] == ["高净值客户"]
    assert tagged.metadata["tagging_method"] == "规则匹配"
    assert tagged.metadata["tagging_version"] == "2026-07-03"
    assert "标签：" in tagged.text


def test_tag_chunk_is_idempotent() -> None:
    chunk = RagChunk(
        chunk_id="chunk-1",
        chunk_type="strategy",
        text="标签：客户需求/保费预算\n知识类型：销售策略\n定义：处理保费预算异议",
        metadata={"case_name": "案例A"},
        citations=[],
        source_file="case.sales_insights.json",
    )

    tagged = tag_chunk(tag_chunk(chunk))

    assert tagged.text.count("标签：") == 1
    assert tagged.metadata["knowledge_type"] == "销售策略"
    assert "客户需求/保费预算" in tagged.metadata["tag_paths"]
    assert "销售技能/异议处理/保费异议处理" in tagged.metadata["tag_paths"]


def test_tag_chunk_marks_product_and_compliance_risk() -> None:
    chunk = RagChunk(
        chunk_id="chunk-1",
        chunk_type="objection_handling",
        text="客户担心理赔难，销售不能承诺保证理赔，也不能说收益一定高。",
        metadata={"case_name": "案例A", "objection": "理赔难"},
        citations=[],
        source_file="case.sales_insights.json",
    )

    tagged = tag_chunk(chunk)

    assert tagged.metadata["knowledge_type"] == "异议处理"
    assert "销售技能/异议处理/理赔顾虑异议处理" in tagged.metadata["tag_paths"]
    assert tagged.metadata["objection_types"] == ["理赔顾虑"]
    assert tagged.metadata["compliance_risks"] == [
        "收益承诺风险",
        "理赔承诺风险",
    ]


def test_render_tag_line_returns_empty_without_paths() -> None:
    assert render_tag_line({"tag_paths": []}) == ""
