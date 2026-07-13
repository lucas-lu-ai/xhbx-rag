import json
from pathlib import Path

import xhbx_rag.web.bad_cases as bad_cases


def _bad_case_payload() -> dict:
    return {
        "query": "保单整理对客户有什么作用？",
        "rewritten_query": "保单整理客户价值",
        "answer": "保单整理能帮助客户看清保障缺口。",
        "top_n": 20,
        "top_k": 5,
        "issue_types": ["missing_knowledge", "ranking_wrong"],
        "expected_knowledge": "应该命中保障缺口分析的案例片段。",
        "expected_source": "data/案例A/第3节.track-0.txt",
        "note": "当前前两条都是销售动作，缺少客户侧收益。",
        "citations": [{"filename": "第1节.track-0.txt"}],
        "retrieval_evidences": [
            {
                "chunk_id": "case-a-1",
                "chunk_type": "strategy",
                "text": "先做保单整理。",
            }
        ],
    }


def test_save_bad_case_appends_jsonl_record(tmp_path: Path) -> None:
    result = bad_cases.save_bad_case(_bad_case_payload(), project_root=tmp_path)

    path = tmp_path / ".local" / "bad_cases" / "bad_cases.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert result["ok"] is True
    assert result["bad_case_id"] == records[0]["bad_case_id"]
    assert result["path"] == str(path)
    assert records[0]["query"] == "保单整理对客户有什么作用？"
    assert records[0]["issue_types"] == ["missing_knowledge", "ranking_wrong"]
    assert records[0]["retrieval_evidences"][0]["chunk_id"] == "case-a-1"
    assert records[0]["citations"][0]["filename"] == "第1节.track-0.txt"
    assert "created_at" in records[0]


def test_save_bad_case_adds_chinese_labels_for_review_fields(tmp_path: Path) -> None:
    bad_cases.save_bad_case(
        {
            **_bad_case_payload(),
            "feedback_result": "incomplete",
            "problem_tags": ["missing_talk_track", "compliance_risk"],
            "issue_types": ["incomplete", "missing_talk_track", "compliance_risk"],
            "evidence_feedback": [
                {
                    "chunk_id": "case-a-1",
                    "judgement": "should_use",
                    "label": "案例A · 需求分析",
                    "text_preview": "客户需要先看清保障缺口。",
                },
                {
                    "chunk_id": "case-b-2",
                    "judgement": "should_not_use",
                    "label": "案例B · 销售动作",
                    "text_preview": "先介绍销售流程。",
                },
                {
                    "chunk_id": "case-c-3",
                    "judgement": "ranking_low",
                    "label": "案例C · 缺口分析",
                    "text_preview": "缺口分析应更靠前。",
                },
            ],
        },
        project_root=tmp_path,
    )

    path = tmp_path / ".local" / "bad_cases" / "bad_cases.jsonl"
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    assert record["feedback_result"] == "incomplete"
    assert record["feedback_result_label"] == "不完整"
    assert record["problem_tags"] == ["missing_talk_track", "compliance_risk"]
    assert record["problem_tag_labels"] == ["缺关键话术", "可能有合规风险"]
    assert record["issue_types"] == [
        "incomplete",
        "missing_talk_track",
        "compliance_risk",
    ]
    assert record["issue_type_labels"] == ["不完整", "缺关键话术", "可能有合规风险"]
    assert record["evidence_feedback"][0]["judgement"] == "should_use"
    assert record["evidence_feedback"][0]["judgement_label"] == "应该用"
    assert record["evidence_feedback"][1]["judgement"] == "should_not_use"
    assert record["evidence_feedback"][1]["judgement_label"] == "不该用"
    assert record["evidence_feedback"][2]["judgement"] == "ranking_low"
    assert record["evidence_feedback"][2]["judgement_label"] == "该用但排序太低"


def test_save_bad_case_adds_chinese_labels_for_two_dimensional_feedback(
    tmp_path: Path,
) -> None:
    bad_cases.save_bad_case(
        {
            **_bad_case_payload(),
            "evidence_feedback": [
                {
                    "chunk_id": "case-a-1",
                    "retrieval_judgement": "accurate",
                    "answer_usage_judgement": "incorrect",
                    "reason": "回答误用了这段准确召回的证据。",
                }
            ],
        },
        project_root=tmp_path,
    )

    path = tmp_path / ".local" / "bad_cases" / "bad_cases.jsonl"
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    feedback = record["evidence_feedback"][0]

    assert feedback["retrieval_judgement_label"] == "召回准确"
    assert feedback["answer_usage_judgement_label"] == "参考不正确"


def test_save_bad_case_appends_multiple_records(tmp_path: Path) -> None:
    first = bad_cases.save_bad_case(_bad_case_payload(), project_root=tmp_path)
    second = bad_cases.save_bad_case(
        {
            **_bad_case_payload(),
            "query": "客户预算有限怎么办？",
        },
        project_root=tmp_path,
    )

    path = tmp_path / ".local" / "bad_cases" / "bad_cases.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert [record["bad_case_id"] for record in records] == [
        first["bad_case_id"],
        second["bad_case_id"],
    ]
    assert [record["query"] for record in records] == [
        "保单整理对客户有什么作用？",
        "客户预算有限怎么办？",
    ]
