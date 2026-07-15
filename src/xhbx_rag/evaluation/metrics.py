from __future__ import annotations

import math
from collections import Counter
from numbers import Real
from typing import Any

from xhbx_rag.evaluation.models import (
    DeterministicScores,
    EvaluationItem,
    EvaluationResult,
    JudgeResult,
)


TRACE_STATUSES = ("完整支持", "部分支持", "未定位")


def grade_for_score(total: object) -> str:
    if isinstance(total, bool) or not isinstance(total, Real):
        raise ValueError("总分必须是 0 到 100 之间的有限数值")
    value = float(total)
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("总分必须是 0 到 100 之间的有限数值")
    if value >= 85:
        return "优秀"
    if value >= 75:
        return "合格"
    return "不合格"


def score_deterministic(
    item: EvaluationItem,
    response: object,
    chunk_catalog: set[str],
) -> DeterministicScores:
    payload = response if isinstance(response, dict) else {}
    raw_evidences = payload.get("retrieval_evidences", [])
    if not isinstance(raw_evidences, list):
        raw_evidences = []
    evidences = [row for row in raw_evidences if isinstance(row, dict)]
    retrieved_by_index = [str(row.get("chunk_id", "")).strip() for row in evidences]
    retrieved = [chunk_id for chunk_id in retrieved_by_index if chunk_id]

    if item.trace_status == "未定位":
        non_empty_score = 5.0 if retrieved else 0.0
        valid_count = sum(chunk_id in chunk_catalog for chunk_id in retrieved)
        catalog_ratio = valid_count / len(retrieved) if retrieved else 0.0
        retrieval_score = non_empty_score + 5.0 * catalog_ratio
        rule_name = "检索证据有效性"
        primary_chunk_hit = False
        gold_chunk_recall = 0.0
    else:
        primary_chunk_hit = bool(
            item.primary_chunk_id and item.primary_chunk_id in retrieved
        )
        gold_chunk_ids = set(item.gold_chunk_ids)
        gold_chunk_recall = (
            len(set(retrieved) & gold_chunk_ids) / len(gold_chunk_ids)
            if gold_chunk_ids
            else 0.0
        )
        retrieval_score = (
            5.0 * float(primary_chunk_hit) + 5.0 * gold_chunk_recall
        )
        rule_name = "黄金来源命中"

    raw_citations = payload.get("citations", [])
    if not isinstance(raw_citations, list):
        raw_citations = []
    citations = [row for row in raw_citations if isinstance(row, dict)]
    mapped_indexes: list[int] = []
    for citation in citations:
        raw_index = citation.get("evidence_index", "")
        if not str(raw_index).isdigit():
            continue
        try:
            mapped_indexes.append(int(raw_index))
        except (TypeError, ValueError, OverflowError):
            continue
    valid_indexes = [
        index for index in mapped_indexes if 1 <= index <= len(evidences)
    ]
    valid_ratio = len(valid_indexes) / len(citations) if citations else 0.0
    locatable_ratio = (
        sum(
            bool(citation.get("source_path") and citation.get("locator"))
            for citation in citations
        )
        / len(citations)
        if citations
        else 0.0
    )
    if item.trace_status == "未定位":
        citation_score = 2.5 * valid_ratio + 2.5 * locatable_ratio
    else:
        cited_chunk_ids = {
            retrieved_by_index[index - 1]
            for index in valid_indexes
            if retrieved_by_index[index - 1]
        }
        citation_score = 2.5 * valid_ratio + 2.5 * float(
            bool(cited_chunk_ids & set(item.gold_chunk_ids))
        )

    return DeterministicScores(
        retrieval_score=round(retrieval_score, 2),
        citation_score=round(citation_score, 2),
        total=round(retrieval_score + citation_score, 2),
        rule_name=rule_name,
        primary_chunk_hit=primary_chunk_hit,
        gold_chunk_recall=gold_chunk_recall,
        retrieved_chunk_ids=retrieved,
    )


def aggregate_result(
    *,
    item: EvaluationItem,
    answer_response: dict[str, Any],
    duration_seconds: float,
    deterministic_scores: DeterministicScores | None = None,
    judge_result: JudgeResult | None = None,
    answer_error: str = "",
    judge_error: str = "",
) -> EvaluationResult:
    answer_value = answer_response.get("answer", "")
    answer = answer_value if isinstance(answer_value, str) else str(answer_value or "")
    common = {
        "item_id": item.item_id,
        "excel_row": item.excel_row,
        "question": item.question,
        "reference_answer": item.reference_answer,
        "trace_status": item.trace_status,
        "answer": answer,
        "answer_response": answer_response,
        "duration_seconds": duration_seconds,
    }

    if answer_error:
        return EvaluationResult(
            **common,
            total_score=0,
            grade="问答失败",
            status="问答失败",
            error_tags=["问答执行失败"],
            error_summary=answer_error,
        )
    if judge_error:
        return EvaluationResult(
            **common,
            deterministic_scores=deterministic_scores,
            total_score=None,
            grade="评测失败",
            status="评测失败",
            error_tags=["裁判执行失败"],
            error_summary=judge_error,
        )
    if deterministic_scores is None or judge_result is None:
        raise ValueError("成功聚合必须同时提供确定性指标和裁判结果")

    total_score = round(
        judge_result.correctness_score
        + judge_result.keypoint_coverage_score
        + judge_result.groundedness_score
        + judge_result.relevance_clarity_score
        + deterministic_scores.total,
        2,
    )
    return EvaluationResult(
        **common,
        deterministic_scores=deterministic_scores,
        judge_result=judge_result,
        total_score=total_score,
        grade=grade_for_score(total_score),
        status="已完成",
        error_tags=list(judge_result.error_tags),
        error_summary=judge_result.reason,
    )


def summarize_results(results: list[EvaluationResult]) -> dict[str, Any]:
    total_count = len(results)
    evaluation_failure_count = sum(
        result.status == "评测失败" for result in results
    )
    answer_failure_count = sum(result.status == "问答失败" for result in results)
    valid_scores = [
        result.total_score for result in results if result.total_score is not None
    ]
    passed_count = sum(score >= 75 for score in valid_scores)
    excellent_count = sum(score >= 85 for score in valid_scores)
    effective_count = total_count - evaluation_failure_count
    deterministic_rows = [
        result.deterministic_scores
        for result in results
        if result.deterministic_scores is not None
    ]
    traced_deterministic_rows = [
        result.deterministic_scores
        for result in results
        if result.trace_status != "未定位"
        and result.deterministic_scores is not None
    ]

    trace_layers: dict[str, dict[str, int | float]] = {}
    for trace_status in TRACE_STATUSES:
        layer_results = [
            result for result in results if result.trace_status == trace_status
        ]
        layer_scores = [
            result.total_score
            for result in layer_results
            if result.total_score is not None
        ]
        layer_passed_count = sum(score >= 75 for score in layer_scores)
        trace_layers[trace_status] = {
            "数量": len(layer_results),
            "通过数": layer_passed_count,
            "通过率": _ratio(layer_passed_count, len(layer_results)),
            "平均分": _average(layer_scores),
        }

    error_counts = Counter(
        tag for result in results for tag in result.error_tags
    )
    sorted_error_counts = dict(
        sorted(error_counts.items(), key=lambda item: (-item[1], item[0]))
    )

    return {
        "总题数": total_count,
        "评测失败数": evaluation_failure_count,
        "问答失败数": answer_failure_count,
        "有效评分题数": len(valid_scores),
        "保守通过率": _ratio(passed_count, total_count),
        "有效通过率": _ratio(passed_count, effective_count),
        "优秀率": _ratio(excellent_count, total_count),
        "问答成功率": _ratio(total_count - answer_failure_count, total_count),
        "平均分": _average(valid_scores),
        "分数P50": _percentile(valid_scores, 0.5),
        "分数P95": _percentile(valid_scores, 0.95),
        "证据指标": {
            "主chunk命中率": _ratio(
                sum(
                    score.primary_chunk_hit
                    for score in traced_deterministic_rows
                ),
                len(traced_deterministic_rows),
            ),
            "平均黄金chunk召回率": _mean_ratio(
                [
                    score.gold_chunk_recall
                    for score in traced_deterministic_rows
                ]
            ),
            "平均引用及黄金来源命中得分": _average(
                [score.total for score in deterministic_rows]
            ),
        },
        "溯源状态分层": trace_layers,
        "错误标签频次": sorted_error_counts,
    }


def _ratio(numerator: int | float, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _mean_ratio(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _average(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return round(ordered[lower_index], 2)
    weight = position - lower_index
    value = ordered[lower_index] + (
        ordered[upper_index] - ordered[lower_index]
    ) * weight
    return round(value, 2)
