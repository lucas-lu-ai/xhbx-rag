from __future__ import annotations

import json
from pathlib import Path

import pytest

from xhbx_rag.evaluation.models import (
    DeterministicScores,
    EvaluationResult,
    JudgeResult,
)
from xhbx_rag.evaluation.reporting import (
    build_backfill_payload,
    safe_backfill,
    write_markdown_report,
)


def _result(
    *,
    excel_row: int = 2,
    score: float = 86.5,
    grade: str = "优秀",
) -> EvaluationResult:
    return EvaluationResult(
        item_id=f"row-{excel_row}",
        excel_row=excel_row,
        question="如何沟通预算？",
        reference_answer="先确认预算。",
        trace_status="完整支持",
        answer="先确认客户预算，再讨论保障缺口。",
        answer_response={},
        duration_seconds=8.2,
        deterministic_scores=DeterministicScores(
            retrieval_score=8,
            citation_score=4.5,
            total=12.5,
            rule_name="黄金来源命中",
            primary_chunk_hit=True,
            gold_chunk_recall=0.5,
            retrieved_chunk_ids=["c1", "c3"],
        ),
        judge_result=JudgeResult(
            correctness_score=30,
            keypoint_coverage_score=18,
            groundedness_score=17,
            relevance_clarity_score=9,
            reference_keypoints=["确认预算"],
            covered_keypoints=["确认预算"],
            missing_keypoints=["明确下一步"],
            unsupported_claims=[],
            error_tags=["关键点缺失"],
            reason="关键点覆盖基本完整。",
            improvement_suggestion="补充下一步行动。",
        ),
        total_score=score,
        grade=grade,
        status="已完成",
        error_tags=["关键点缺失"],
        error_summary="关键点覆盖基本完整。",
    )


def _summary() -> dict:
    return {
        "总题数": 50,
        "平均分": 81.25,
        "保守通过率": 0.76,
        "有效通过率": 0.79,
        "优秀率": 0.32,
        "问答成功率": 0.98,
        "分数P50": 82,
        "分数P95": 92,
        "溯源状态分层": {
            "完整支持": {"数量": 38, "平均分": 84, "通过率": 0.84},
            "部分支持": {"数量": 11, "平均分": 74, "通过率": 0.55},
            "未定位": {"数量": 1, "平均分": 60, "通过率": 0},
        },
        "错误标签频次": {"关键点缺失": 12},
    }


def _run_info() -> dict:
    return {
        "运行ID": "run-1",
        "同模型裁判": True,
        "问答模型名": "answer-model",
        "裁判模型名": "judge-model",
    }


def test_markdown_report_uses_chinese_headings_and_recommendations(
    tmp_path: Path,
) -> None:
    path = write_markdown_report(
        tmp_path,
        _run_info(),
        _summary(),
        [_result(), _result(excel_row=3, score=60, grade="不合格")],
    )

    text = path.read_text(encoding="utf-8")
    assert "# 问答智能体效果评估报告" in text
    assert "## 总体结论" in text
    assert "同模型裁判：是" in text
    assert "## 溯源状态分层" in text
    assert "## 代表性低分案例" in text
    assert "### 检索层" in text
    assert "### 生成层" in text
    assert "### 评测集" in text
    assert "correctness_score" not in text


def test_backfill_payload_uses_fixed_chinese_contract_and_numeric_values() -> None:
    payload = build_backfill_payload(_run_info(), _summary(), [_result()])

    assert list(payload) == ["运行信息", "汇总指标", "逐题结果"]
    row = payload["逐题结果"][0]
    assert list(row) == [
        "Excel行号",
        "智能体回答",
        "事实正确性得分",
        "关键点覆盖得分",
        "证据忠实性得分",
        "引用及黄金来源命中得分",
        "相关性与表达得分",
        "总分",
        "评测等级",
        "耗时（秒）",
        "主chunk命中",
        "黄金chunk召回率",
        "检索chunk_id",
        "扣分原因",
        "错误标签",
        "改进建议",
        "评测状态",
    ]
    assert row["事实正确性得分"] == 30
    assert isinstance(row["事实正确性得分"], (int, float))
    assert row["主chunk命中"] == "是"
    assert row["检索chunk_id"] == "c1；c3"
    assert row["错误标签"] == "关键点缺失"
    assert "correctness_score" not in json.dumps(payload, ensure_ascii=False)


class _FailingVerifyAdapter:
    def extract(self, input_path: Path, output_path: Path) -> Path:
        del input_path
        output_path.write_text('{"工作簿快照": {}}', encoding="utf-8")
        return output_path

    def backfill(
        self,
        input_path: Path,
        payload_path: Path,
        output_path: Path,
    ) -> Path:
        del input_path, payload_path
        output_path.write_bytes(b"candidate")
        return output_path

    def verify(
        self,
        input_path: Path,
        snapshot_path: Path,
        output_path: Path,
        preview_dir: Path,
    ) -> Path:
        del input_path, snapshot_path, output_path, preview_dir
        raise RuntimeError("模拟验证失败")


def test_atomic_backfill_keeps_source_when_verification_fails(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"original")
    run_dir = tmp_path / "run"

    with pytest.raises(ValueError, match="工作簿验证失败"):
        safe_backfill(
            source,
            run_dir,
            _FailingVerifyAdapter(),
            {"运行信息": {}, "汇总指标": {}, "逐题结果": []},
        )

    assert source.read_bytes() == b"original"
    assert (run_dir / "input-backup.xlsx").read_bytes() == b"original"
    assert (run_dir / "待验证.xlsx").read_bytes() == b"candidate"
