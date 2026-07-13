from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .source_paths import project_root_from_module


BAD_CASES_RELATIVE_PATH = Path(".local") / "bad_cases" / "bad_cases.jsonl"

ISSUE_TYPE_LABELS = {
    "usable": "可用",
    "inaccurate": "不准确",
    "incomplete": "不完整",
    "citation_issue": "引用有问题",
    "customer_mismatch": "不适合当前客户",
    "off_topic": "答非所问",
    "missing_talk_track": "缺关键话术",
    "case_mismatch": "案例不匹配",
    "citation_mismatch": "引用/原文对不上",
    "not_customer_ready": "表达不能直接给客户用",
    "compliance_risk": "可能有合规风险",
    "missing_knowledge": "缺少知识",
    "ranking_wrong": "检索排序错误",
    "citation_wrong": "引用错误",
    "answer_unsupported": "回答缺少证据支撑",
    "other": "其他",
}
EVIDENCE_FEEDBACK_JUDGEMENT_LABELS = {
    "should_use": "应该用",
    "should_not_use": "不该用",
    "ranking_low": "该用但排序太低",
}
RETRIEVAL_JUDGEMENT_LABELS = {
    "accurate": "召回准确",
    "inaccurate": "召回不准确",
}
ANSWER_USAGE_JUDGEMENT_LABELS = {
    "correct": "参考正确",
    "incorrect": "参考不正确",
    "not_applicable": "不适用",
}


def save_bad_case(
    payload: Mapping[str, Any],
    *,
    project_root: Path | None = None,
    bad_case_id: str | None = None,
) -> dict[str, Any]:
    root = project_root or project_root_from_module()
    path = root / BAD_CASES_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "bad_case_id": bad_case_id or f"bad-{uuid4().hex}",
        "created_at": datetime.now(UTC).isoformat(),
        **_with_chinese_labels(payload),
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    return {
        "ok": True,
        "bad_case_id": record["bad_case_id"],
        "path": str(path),
    }


def validate_evidence_feedback_items(
    values: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    for value in values:
        has_legacy_judgement = "judgement" in value
        has_new_judgement = (
            "retrieval_judgement" in value or "answer_usage_judgement" in value
        )
        if has_legacy_judgement and has_new_judgement:
            raise ValueError("新旧证据反馈字段不能混用")

        retrieval_judgement = value.get("retrieval_judgement")
        answer_usage_judgement = value.get("answer_usage_judgement")
        if not has_new_judgement:
            judgement = value.get("judgement")
            if (
                not isinstance(judgement, str)
                or judgement not in EVIDENCE_FEEDBACK_JUDGEMENT_LABELS
            ):
                raise ValueError("证据反馈类型不支持")
            continue

        if (
            not isinstance(retrieval_judgement, str)
            or retrieval_judgement not in RETRIEVAL_JUDGEMENT_LABELS
        ):
            raise ValueError("召回判断不支持")
        if (
            not isinstance(answer_usage_judgement, str)
            or answer_usage_judgement not in ANSWER_USAGE_JUDGEMENT_LABELS
        ):
            raise ValueError("回答参考判断不支持")
        if retrieval_judgement == "accurate" and answer_usage_judgement not in {
            "correct",
            "incorrect",
        }:
            raise ValueError("证据反馈组合不支持")
        if (
            retrieval_judgement == "inaccurate"
            and answer_usage_judgement != "not_applicable"
        ):
            raise ValueError("证据反馈组合不支持")
        if (
            retrieval_judgement == "inaccurate"
            or answer_usage_judgement == "incorrect"
        ):
            reason = value.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("负向证据反馈必须填写原因")

    return values


def _with_chinese_labels(payload: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(payload)

    feedback_result = record.get("feedback_result")
    if isinstance(feedback_result, str) and feedback_result:
        record["feedback_result_label"] = _label_for(
            feedback_result,
            ISSUE_TYPE_LABELS,
        )

    problem_tags = record.get("problem_tags")
    if isinstance(problem_tags, list):
        record["problem_tag_labels"] = _labels_for(problem_tags, ISSUE_TYPE_LABELS)

    issue_types = record.get("issue_types")
    if isinstance(issue_types, list):
        record["issue_type_labels"] = _labels_for(issue_types, ISSUE_TYPE_LABELS)

    evidence_feedback = record.get("evidence_feedback")
    if isinstance(evidence_feedback, list):
        record["evidence_feedback"] = [
            _with_evidence_feedback_label(item) for item in evidence_feedback
        ]

    return record


def _labels_for(values: list[Any], labels: Mapping[str, str]) -> list[str]:
    return [_label_for(value, labels) for value in values]


def _label_for(value: Any, labels: Mapping[str, str]) -> str:
    if isinstance(value, str):
        return labels.get(value, value)
    return str(value)


def _with_evidence_feedback_label(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value

    item = dict(value)
    judgement = item.get("judgement")
    if isinstance(judgement, str) and judgement:
        item["judgement_label"] = _label_for(
            judgement,
            EVIDENCE_FEEDBACK_JUDGEMENT_LABELS,
        )

    retrieval_judgement = item.get("retrieval_judgement")
    if isinstance(retrieval_judgement, str) and retrieval_judgement:
        item["retrieval_judgement_label"] = _label_for(
            retrieval_judgement,
            RETRIEVAL_JUDGEMENT_LABELS,
        )

    answer_usage_judgement = item.get("answer_usage_judgement")
    if isinstance(answer_usage_judgement, str) and answer_usage_judgement:
        item["answer_usage_judgement_label"] = _label_for(
            answer_usage_judgement,
            ANSWER_USAGE_JUDGEMENT_LABELS,
        )
    return item
