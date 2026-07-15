from __future__ import annotations

import math
import re

import pytest

from xhbx_rag.evaluation.metrics import (
    aggregate_result,
    grade_for_score,
    score_deterministic,
    summarize_results,
)
from xhbx_rag.evaluation.models import (
    DeterministicScores,
    EvaluationItem,
    EvaluationResult,
    JudgeResult,
)


def _item(
    *,
    item_id: str = "row-2",
    excel_row: int = 2,
    primary: str = "c1",
    gold: list[str] | None = None,
    status: str = "完整支持",
) -> EvaluationItem:
    return EvaluationItem(
        item_id=item_id,
        excel_row=excel_row,
        question="如何沟通预算？",
        reference_answer="先确认客户预算。",
        trace_status=status,
        primary_chunk_id=primary,
        gold_chunk_ids=gold if gold is not None else ["c1", "c2"],
    )


def _judge(**overrides: object) -> JudgeResult:
    values: dict[str, object] = {
        "correctness_score": 30,
        "keypoint_coverage_score": 18,
        "groundedness_score": 17,
        "relevance_clarity_score": 9,
        "reference_keypoints": ["先确认预算"],
        "covered_keypoints": ["先确认预算"],
        "missing_keypoints": [],
        "unsupported_claims": [],
        "error_tags": ["关键点缺失"],
        "reason": "回答基本正确，但缺少后续行动。",
        "improvement_suggestion": "补充后续行动。",
    }
    values.update(overrides)
    return JudgeResult(**values)


def _scores(
    *,
    total: float = 10,
    primary_chunk_hit: bool = True,
    gold_chunk_recall: float = 0.5,
) -> DeterministicScores:
    retrieval_score = min(total, 10)
    return DeterministicScores(
        retrieval_score=retrieval_score,
        citation_score=total - retrieval_score,
        total=total,
        rule_name="黄金来源命中",
        primary_chunk_hit=primary_chunk_hit,
        gold_chunk_recall=gold_chunk_recall,
        retrieved_chunk_ids=["c1"],
    )


def _result(
    index: int,
    *,
    trace_status: str = "完整支持",
    total_score: float | None = 80,
    grade: str = "合格",
    status: str = "已完成",
    error_tags: list[str] | None = None,
    deterministic_scores: DeterministicScores | None = None,
) -> EvaluationResult:
    return EvaluationResult(
        item_id=f"row-{index + 2}",
        excel_row=index + 2,
        question=f"问题 {index + 1}",
        reference_answer="参考答案",
        trace_status=trace_status,
        answer="智能体回答",
        duration_seconds=1,
        deterministic_scores=deterministic_scores,
        total_score=total_score,
        grade=grade,
        status=status,
        error_tags=error_tags or [],
    )


def test_deterministic_score_for_traced_item() -> None:
    response = {
        "retrieval_evidences": [{"chunk_id": "c1"}, {"chunk_id": "c3"}],
        "citations": [
            {
                "evidence_index": 1,
                "source_path": "case/a.txt",
                "locator": {"line_start": 2},
            }
        ],
    }

    score = score_deterministic(_item(), response, {"c1", "c2", "c3"})

    assert score.primary_chunk_hit is True
    assert score.gold_chunk_recall == 0.5
    assert score.retrieval_score == 7.5
    assert score.citation_score == 5.0
    assert score.total == 12.5
    assert score.rule_name == "黄金来源命中"


def test_unlocated_item_uses_catalog_validity_not_gold_hit() -> None:
    item = _item(primary="", gold=[], status="未定位")
    response = {
        "retrieval_evidences": [{"chunk_id": "known"}],
        "citations": [],
    }

    score = score_deterministic(item, response, {"known"})

    assert score.rule_name == "检索证据有效性"
    assert score.retrieval_score == 10.0
    assert score.citation_score == 0.0
    assert score.primary_chunk_hit is False
    assert score.gold_chunk_recall == 0.0


def test_deterministic_score_preserves_duplicate_evidence_order_and_uses_dict_citations() -> None:
    response = {
        "retrieval_evidences": [
            {"chunk_id": "c2"},
            "不是证据字典",
            {"chunk_id": "c2"},
            {"chunk_id": "c1"},
        ],
        "citations": [
            {"evidence_index": "1"},
            {"evidence_index": 3},
            {"evidence_index": "非法"},
            "不是引用字典",
        ],
    }

    score = score_deterministic(_item(), response, {"c1", "c2"})

    assert score.retrieved_chunk_ids == ["c2", "c2", "c1"]
    assert score.gold_chunk_recall == 1.0
    assert score.retrieval_score == 10.0
    assert score.citation_score == 4.17
    assert score.total == 14.17


@pytest.mark.parametrize(
    "response",
    [
        [],
        {"retrieval_evidences": "不是列表", "citations": "不是列表"},
        {
            "retrieval_evidences": [None, "非法证据"],
            "citations": [
                None,
                {"evidence_index": True},
                {"evidence_index": 1.0},
                {"evidence_index": -1},
                {"evidence_index": "999"},
            ],
        },
    ],
)
def test_deterministic_score_tolerates_malformed_collections_and_indexes(
    response: object,
) -> None:
    score = score_deterministic(_item(), response, {"c1", "c2"})

    assert 0 <= score.total <= 15
    assert score.retrieved_chunk_ids == []


def test_bool_evidence_index_is_not_treated_as_index_one() -> None:
    score = score_deterministic(
        _item(),
        {
            "retrieval_evidences": [{"chunk_id": "c1"}],
            "citations": [{"evidence_index": True}],
        },
        {"c1", "c2"},
    )

    assert score.retrieval_score == 7.5
    assert score.citation_score == 0.0
    assert score.total == 7.5


@pytest.mark.parametrize(
    ("total", "grade"),
    [(85, "优秀"), (84.99, "合格"), (75, "合格"), (74.99, "不合格")],
)
def test_grade_boundaries(total: float, grade: str) -> None:
    assert grade_for_score(total) == grade


@pytest.mark.parametrize(
    "invalid",
    [True, False, -0.01, 100.01, math.nan, math.inf, -math.inf],
)
def test_grade_rejects_bool_non_finite_and_out_of_range(invalid: object) -> None:
    with pytest.raises(ValueError, match="总分"):
        grade_for_score(invalid)


def test_aggregate_result_combines_all_five_scores_and_uses_judge_tags() -> None:
    deterministic = _scores(total=12.35)
    judge = _judge(
        correctness_score=30.1,
        keypoint_coverage_score=18.2,
        groundedness_score=17.3,
        relevance_clarity_score=9.4,
    )
    response = {"answer": "最终回答", "retrieval_evidences": []}

    result = aggregate_result(
        item=_item(),
        answer_response=response,
        duration_seconds=2.345,
        deterministic_scores=deterministic,
        judge_result=judge,
    )

    assert result.answer == "最终回答"
    assert result.answer_response == response
    assert result.total_score == 87.35
    assert result.grade == "优秀"
    assert result.status == "已完成"
    assert result.error_tags == ["关键点缺失"]
    assert result.error_summary == judge.reason


def test_aggregate_result_records_answer_failure_as_zero() -> None:
    result = aggregate_result(
        item=_item(),
        answer_response={},
        duration_seconds=3,
        answer_error="问答超时",
    )

    assert result.total_score == 0
    assert result.grade == "问答失败"
    assert result.status == "问答失败"
    assert result.error_tags == ["问答执行失败"]
    assert result.error_summary == "问答超时"
    assert result.deterministic_scores is None
    assert result.judge_result is None


def test_aggregate_result_keeps_real_rules_but_does_not_invent_judge_score_on_failure() -> None:
    deterministic = _scores(total=12.5)
    result = aggregate_result(
        item=_item(),
        answer_response={"answer": "已有回答"},
        duration_seconds=4,
        deterministic_scores=deterministic,
        judge_error="裁判响应格式错误",
    )

    assert result.answer == "已有回答"
    assert result.total_score is None
    assert result.grade == "评测失败"
    assert result.status == "评测失败"
    assert result.error_tags == ["裁判执行失败"]
    assert result.error_summary == "裁判响应格式错误"
    assert result.deterministic_scores == deterministic
    assert result.judge_result is None


@pytest.mark.parametrize(
    ("deterministic", "judge"),
    [(_scores(), None), (None, _judge()), (None, None)],
)
def test_aggregate_result_requires_both_score_objects_on_success(
    deterministic: DeterministicScores | None,
    judge: JudgeResult | None,
) -> None:
    with pytest.raises(ValueError, match="成功聚合必须同时提供"):
        aggregate_result(
            item=_item(),
            answer_response={"answer": "回答"},
            duration_seconds=1,
            deterministic_scores=deterministic,
            judge_result=judge,
        )


def test_summarize_fifty_results_keeps_failures_in_conservative_denominator() -> None:
    results: list[EvaluationResult] = []
    for index in range(50):
        trace_status = (
            "完整支持" if index < 38 else "部分支持" if index < 49 else "未定位"
        )
        deterministic = None if index == 2 else _scores()
        if index < 2:
            results.append(
                _result(
                    index,
                    trace_status=trace_status,
                    total_score=None,
                    grade="评测失败",
                    status="评测失败",
                    error_tags=["裁判执行失败"],
                    deterministic_scores=deterministic,
                )
            )
        elif index == 2:
            results.append(
                _result(
                    index,
                    trace_status=trace_status,
                    total_score=0,
                    grade="问答失败",
                    status="问答失败",
                    error_tags=["问答执行失败"],
                )
            )
        elif index < 8:
            results.append(
                _result(
                    index,
                    trace_status=trace_status,
                    total_score=90,
                    grade="优秀",
                    deterministic_scores=deterministic,
                )
            )
        elif index < 33:
            results.append(
                _result(
                    index,
                    trace_status=trace_status,
                    total_score=80,
                    grade="合格",
                    deterministic_scores=deterministic,
                )
            )
        else:
            results.append(
                _result(
                    index,
                    trace_status=trace_status,
                    total_score=50,
                    grade="不合格",
                    deterministic_scores=deterministic,
                )
            )

    summary = summarize_results(results)

    assert summary["总题数"] == 50
    assert summary["评测失败数"] == 2
    assert summary["问答失败数"] == 1
    assert summary["有效评分题数"] == 48
    assert summary["保守通过率"] == 0.6
    assert summary["有效通过率"] == 0.625
    assert summary["优秀率"] == 0.1
    assert summary["问答成功率"] == 0.98
    assert summary["平均分"] == 68.75
    assert summary["分数P50"] == 80.0
    assert summary["分数P95"] == 90.0
    assert summary["证据指标"] == {
        "主chunk命中率": 1.0,
        "平均黄金chunk召回率": 0.5,
        "平均引用及黄金来源命中得分": 10.0,
    }
    assert summary["溯源状态分层"] == {
        "完整支持": {
            "数量": 38,
            "通过数": 30,
            "通过率": 30 / 38,
            "平均分": 75.0,
        },
        "部分支持": {
            "数量": 11,
            "通过数": 0,
            "通过率": 0.0,
            "平均分": 50.0,
        },
        "未定位": {
            "数量": 1,
            "通过数": 0,
            "通过率": 0.0,
            "平均分": 50.0,
        },
    }
    assert summary["错误标签频次"] == {"裁判执行失败": 2, "问答执行失败": 1}


def test_evidence_rates_use_only_traced_rows_but_rule_average_uses_all_rows() -> None:
    summary = summarize_results(
        [
            _result(
                0,
                trace_status="完整支持",
                deterministic_scores=_scores(
                    total=2,
                    primary_chunk_hit=True,
                    gold_chunk_recall=0,
                ),
            ),
            _result(
                1,
                trace_status="部分支持",
                total_score=None,
                grade="评测失败",
                status="评测失败",
                error_tags=["裁判执行失败"],
                deterministic_scores=_scores(
                    total=7,
                    primary_chunk_hit=False,
                    gold_chunk_recall=1,
                ),
            ),
            _result(
                2,
                trace_status="未定位",
                deterministic_scores=_scores(
                    total=15,
                    primary_chunk_hit=True,
                    gold_chunk_recall=1,
                ),
            ),
        ]
    )

    assert summary["证据指标"] == {
        "主chunk命中率": 0.5,
        "平均黄金chunk召回率": 0.5,
        "平均引用及黄金来源命中得分": 8.0,
    }


def test_average_gold_chunk_recall_preserves_exact_two_thirds_ratio() -> None:
    summary = summarize_results(
        [
            _result(
                0,
                deterministic_scores=_scores(gold_chunk_recall=0),
            ),
            _result(
                1,
                trace_status="部分支持",
                deterministic_scores=_scores(gold_chunk_recall=1),
            ),
            _result(
                2,
                deterministic_scores=_scores(gold_chunk_recall=1),
            ),
        ]
    )

    assert summary["证据指标"]["平均黄金chunk召回率"] == 2 / 3


def test_answer_failure_zero_changes_linear_interpolated_percentiles() -> None:
    summary = summarize_results(
        [
            _result(
                0,
                total_score=0,
                grade="问答失败",
                status="问答失败",
                error_tags=["问答执行失败"],
            ),
            _result(1, total_score=80, grade="合格"),
            _result(2, total_score=90, grade="优秀"),
            _result(3, total_score=100, grade="优秀"),
        ]
    )

    assert summary["有效评分题数"] == 4
    assert summary["分数P50"] == 85.0
    assert summary["分数P95"] == 98.5


def test_summarize_empty_results_has_zero_values_and_all_trace_layers() -> None:
    summary = summarize_results([])

    assert summary["总题数"] == 0
    assert summary["保守通过率"] == 0
    assert summary["有效通过率"] == 0
    assert summary["平均分"] == 0
    assert set(summary["溯源状态分层"]) == {"完整支持", "部分支持", "未定位"}
    for layer in summary["溯源状态分层"].values():
        assert layer == {"数量": 0, "通过数": 0, "通过率": 0, "平均分": 0}


def test_summary_has_exact_chinese_business_key_sets() -> None:
    summary = summarize_results([])

    assert set(summary) == {
        "总题数",
        "评测失败数",
        "问答失败数",
        "有效评分题数",
        "保守通过率",
        "有效通过率",
        "优秀率",
        "问答成功率",
        "平均分",
        "分数P50",
        "分数P95",
        "证据指标",
        "溯源状态分层",
        "错误标签频次",
    }
    assert set(summary["证据指标"]) == {
        "主chunk命中率",
        "平均黄金chunk召回率",
        "平均引用及黄金来源命中得分",
    }
    assert set(summary["溯源状态分层"]) == {
        "完整支持",
        "部分支持",
        "未定位",
    }
    for layer in summary["溯源状态分层"].values():
        assert set(layer) == {"数量", "通过数", "通过率", "平均分"}

    allowed_technical_keys = {
        "分数P50",
        "分数P95",
        "主chunk命中率",
        "平均黄金chunk召回率",
    }

    def assert_no_unapproved_english(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                assert re.search(r"[\u4e00-\u9fff]", key), key
                if key not in allowed_technical_keys:
                    assert not re.search(r"[A-Za-z]", key), key
                assert_no_unapproved_english(child)
        elif isinstance(value, list):
            for child in value:
                assert_no_unapproved_english(child)

    assert_no_unapproved_english(summary)
