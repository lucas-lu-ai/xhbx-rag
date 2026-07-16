from __future__ import annotations

import pytest

from xhbx_rag.knowledge_domain import (
    CANONICAL_DOMAINS,
    DOMAIN_TAGGING_VERSION,
    apply_domain_metadata,
    infer_chunk_domains,
    infer_query_domains,
    validate_domain_metadata,
)
from xhbx_rag.models import RagChunk


def _chunk(
    *,
    text: str = "普通培训内容",
    metadata: dict | None = None,
    source_file: str = "培训材料",
) -> RagChunk:
    return RagChunk(
        chunk_id="chunk-1",
        chunk_type="knowledge_entry",
        text=text,
        metadata=metadata or {},
        citations=[],
        source_file=source_file,
    )


@pytest.mark.parametrize("domain", CANONICAL_DOMAINS)
def test_every_canonical_domain_is_accepted_as_structured_metadata(domain: str) -> None:
    result = infer_chunk_domains(_chunk(metadata={"tags": [domain]}))

    assert result is not None
    assert result.primary_domain == domain
    assert result.domain_tags == [domain]
    assert result.scores[domain] == 10


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("这款重疾险的保障责任是什么", "产品知识"),
        ("保险法要求如何避免销售误导", "合规与风控"),
        ("客户提出异议时如何促成", "销售技能"),
        ("老客户服务和转介绍怎么做", "客户经营"),
        ("新华保险的公司品牌介绍", "行业与公司"),
        ("如何做好时间管理和目标管理", "个人成长"),
        ("主管如何进行增员和团队管理", "组织发展"),
    ],
)
def test_query_domain_rules_cover_all_domains(text: str, expected: str) -> None:
    assert expected in infer_query_domains(text)


def test_query_domain_rules_do_not_add_unmatched_domains() -> None:
    assert infer_query_domains("今天天气怎么样") == []


def test_multi_domain_tie_uses_risk_first_primary_and_stable_order() -> None:
    result = infer_chunk_domains(
        _chunk(metadata={"tags": ["产品知识", "合规与风控"]})
    )

    assert result is not None
    assert result.primary_domain == "合规与风控"
    assert result.domain_tags == ["合规与风控", "产品知识"]


def test_structured_keyword_outweighs_repeated_body_keywords() -> None:
    result = infer_chunk_domains(
        _chunk(
            metadata={"title": "客户关系维护与转介绍"},
            text="保险法、监管、反洗钱和销售误导是正文里的风险内容。",
        )
    )

    assert result is not None
    assert result.primary_domain == "客户经营"
    assert result.scores["客户经营"] == 8
    assert result.scores["合规与风控"] == 4


def test_single_weak_body_hit_is_unclassified() -> None:
    assert infer_chunk_domains(_chunk(text="这里只提到一次合规")) is None


def test_existing_business_domains_are_treated_as_reliable_structured_labels() -> None:
    result = infer_chunk_domains(
        _chunk(metadata={"business_domains": ["客户经营", "销售技能"]})
    )

    assert result is not None
    assert result.primary_domain == "客户经营"
    assert result.domain_tags == ["客户经营", "销售技能"]


def test_apply_domain_metadata_preserves_chunk_and_is_idempotent() -> None:
    chunk = _chunk(metadata={"title": "保险产品保障责任", "legacy": "保留"})
    classification = infer_chunk_domains(chunk)
    assert classification is not None

    first = apply_domain_metadata(chunk, classification, "培训资料")
    second_classification = infer_chunk_domains(first)
    assert second_classification is not None
    second = apply_domain_metadata(first, second_classification, "培训资料")

    assert second == first
    assert first.chunk_id == chunk.chunk_id
    assert first.text == chunk.text
    assert first.source_file == chunk.source_file
    assert first.metadata["legacy"] == "保留"
    assert first.metadata["domain_tagging_version"] == DOMAIN_TAGGING_VERSION


def test_validate_domain_metadata_reports_contract_violations() -> None:
    errors = validate_domain_metadata(
        {
            "source_kind": "未知",
            "primary_domain": "产品知识",
            "domain_tags": ["客户经营", "客户经营", "二级标签"],
            "domain_tagging_method": "模型打标",
            "domain_tagging_version": "old",
        }
    )

    assert any("source_kind" in error for error in errors)
    assert any("primary_domain 必须包含" in error for error in errors)
    assert any("domain_tags 必须去重" in error for error in errors)
    assert any("不支持的一级标签" in error for error in errors)
    assert any("domain_tagging_method" in error for error in errors)
    assert any("domain_tagging_version" in error for error in errors)
