import json

import pytest

from xhbx_rag.normalizer import make_case_id, normalize_case
from xhbx_rag.parser import ParseFatalError, parse_inputs


def test_parse_inputs_warns_when_playbook_is_missing(tmp_path) -> None:
    insights = tmp_path / "case.sales_insights.json"
    insights.write_text(
        json.dumps({"case_name": "案例A", "case_summary": "摘要"}, ensure_ascii=False),
        encoding="utf-8",
    )

    parsed = parse_inputs(insights, None)

    assert parsed.source.case_name == "案例A"
    assert "未提供 case.sales_playbook.md" in parsed.warnings


def test_parse_inputs_fails_when_case_name_missing(tmp_path) -> None:
    insights = tmp_path / "case.sales_insights.json"
    insights.write_text(
        json.dumps({"case_summary": "摘要"}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ParseFatalError, match="case_name"):
        parse_inputs(insights, None)


def test_parse_inputs_warns_for_missing_top_level_lists(tmp_path) -> None:
    insights = tmp_path / "case.sales_insights.json"
    insights.write_text(
        json.dumps({"case_name": "案例A", "case_summary": "摘要"}, ensure_ascii=False),
        encoding="utf-8",
    )

    parsed = parse_inputs(insights, None)

    assert "缺少 customer_journey，已按空列表处理" in parsed.warnings
    assert "缺少 strategies，已按空列表处理" in parsed.warnings
    assert "缺少 scripts，已按空列表处理" in parsed.warnings
    assert "缺少 objection_handling，已按空列表处理" in parsed.warnings


def test_normalize_case_sets_source_files_and_stable_case_id(tmp_path) -> None:
    insights = tmp_path / "case.sales_insights.json"
    playbook = tmp_path / "case.sales_playbook.md"
    insights.write_text(
        json.dumps(
            {"case_name": "米丽霞/百万标保", "case_summary": "摘要"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    playbook.write_text("# 米丽霞/百万标保 - 销售洞察手册\n", encoding="utf-8")
    parsed = parse_inputs(insights, playbook)

    knowledge = normalize_case(parsed)

    assert knowledge.case_id == make_case_id("米丽霞/百万标保")
    assert knowledge.source_files == [
        "case.sales_insights.json",
        "case.sales_playbook.md",
    ]
