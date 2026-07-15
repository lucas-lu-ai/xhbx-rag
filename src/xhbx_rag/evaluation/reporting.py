from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from xhbx_rag.evaluation.models import EvaluationResult


class WorkbookBackfillAdapter(Protocol):
    def extract(self, input_path: Path, output_path: Path) -> Path: ...

    def backfill(
        self,
        input_path: Path,
        payload_path: Path,
        output_path: Path,
    ) -> Path: ...

    def verify(
        self,
        input_path: Path,
        snapshot_path: Path,
        output_path: Path,
        preview_dir: Path,
    ) -> Path: ...


def build_backfill_payload(
    run_info: Mapping[str, object],
    summary: Mapping[str, object],
    results: Sequence[EvaluationResult],
) -> dict[str, object]:
    return {
        "运行信息": dict(run_info),
        "汇总指标": dict(summary),
        "逐题结果": [_backfill_row(result) for result in results],
    }


def _backfill_row(result: EvaluationResult) -> dict[str, object]:
    deterministic = result.deterministic_scores
    judge = result.judge_result
    return {
        "Excel行号": result.excel_row,
        "智能体回答": result.answer,
        "事实正确性得分": (
            judge.correctness_score if judge is not None else None
        ),
        "关键点覆盖得分": (
            judge.keypoint_coverage_score if judge is not None else None
        ),
        "证据忠实性得分": (
            judge.groundedness_score if judge is not None else None
        ),
        "引用及黄金来源命中得分": (
            deterministic.total if deterministic is not None else None
        ),
        "相关性与表达得分": (
            judge.relevance_clarity_score if judge is not None else None
        ),
        "总分": result.total_score,
        "评测等级": result.grade,
        "耗时（秒）": round(result.duration_seconds, 2),
        "主chunk命中": (
            "是" if deterministic is not None and deterministic.primary_chunk_hit else "否"
        ),
        "黄金chunk召回率": (
            deterministic.gold_chunk_recall if deterministic is not None else 0.0
        ),
        "检索chunk_id": (
            "；".join(deterministic.retrieved_chunk_ids)
            if deterministic is not None
            else ""
        ),
        "扣分原因": judge.reason if judge is not None else result.error_summary,
        "错误标签": "；".join(result.error_tags),
        "改进建议": (
            judge.improvement_suggestion
            if judge is not None
            else _failure_suggestion(result)
        ),
        "评测状态": result.status,
    }


def _failure_suggestion(result: EvaluationResult) -> str:
    if result.status == "问答失败":
        return "检查问答模型、检索服务和网络连接后重试。"
    if result.status == "评测失败":
        return "保留问答结果并仅重跑裁判步骤。"
    return ""


def write_markdown_report(
    run_dir: Path,
    run_info: Mapping[str, object],
    summary: Mapping[str, object],
    results: Sequence[EvaluationResult],
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "report.md"
    low_scores = sorted(
        (
            result
            for result in results
            if result.status != "已完成"
            or result.grade not in {"优秀", "合格"}
        ),
        key=lambda result: (
            result.total_score is None,
            result.total_score if result.total_score is not None else 101,
            result.excel_row,
        ),
    )[:5]
    average = _number(summary.get("平均分"))
    conservative_pass_rate = _ratio_text(summary.get("保守通过率"))
    conclusion = (
        f"本次共评测 {_integer(summary.get('总题数'))} 题，平均分 "
        f"{average:.2f}，保守通过率 {conservative_pass_rate}。"
    )
    lines = [
        "# 问答智能体效果评估报告",
        "",
        "## 总体结论",
        "",
        conclusion,
        "",
        f"同模型裁判：{_yes_no(run_info.get('同模型裁判'))}",
        "",
        "## 总体指标",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| 总题数 | {_integer(summary.get('总题数'))} |",
        f"| 平均分 | {average:.2f} |",
        f"| 保守通过率 | {conservative_pass_rate} |",
        f"| 有效通过率 | {_ratio_text(summary.get('有效通过率'))} |",
        f"| 优秀率 | {_ratio_text(summary.get('优秀率'))} |",
        f"| 问答成功率 | {_ratio_text(summary.get('问答成功率'))} |",
        f"| 分数P50 | {_number(summary.get('分数P50')):.2f} |",
        f"| 分数P95 | {_number(summary.get('分数P95')):.2f} |",
        "",
        "## 溯源状态分层",
        "",
        "| 溯源状态 | 数量 | 平均分 | 通过率 |",
        "| --- | ---: | ---: | ---: |",
    ]
    layers = summary.get("溯源状态分层", {})
    layer_map = layers if isinstance(layers, Mapping) else {}
    for status in ("完整支持", "部分支持", "未定位"):
        raw_layer = layer_map.get(status, {})
        layer = raw_layer if isinstance(raw_layer, Mapping) else {}
        lines.append(
            f"| {status} | {_integer(layer.get('数量'))} | "
            f"{_number(layer.get('平均分')):.2f} | "
            f"{_ratio_text(layer.get('通过率'))} |"
        )

    lines.extend(["", "## 常见错误", ""])
    error_counts = summary.get("错误标签频次", {})
    if isinstance(error_counts, Mapping) and error_counts:
        for label, count in error_counts.items():
            lines.append(f"- {label}：{_integer(count)} 题")
    else:
        lines.append("- 未发现固定错误标签。")

    lines.extend(["", "## 代表性低分案例", ""])
    if low_scores:
        for result in low_scores:
            score = "无有效分数" if result.total_score is None else f"{result.total_score:.2f} 分"
            reason = (
                result.judge_result.reason
                if result.judge_result is not None
                else result.error_summary
            )
            lines.append(
                f"- Excel 第 {result.excel_row} 行（{score}，{result.grade}）："
                f"{reason or '未提供扣分原因'}"
            )
    else:
        lines.append("- 本次没有不合格或执行失败案例。")

    lines.extend(
        [
            "",
            "## 优化建议",
            "",
            "### 检索层",
            "",
            "优先分析黄金来源未命中和引用缺失案例，校准召回、重排与引用映射。",
            "",
            "### 生成层",
            "",
            "针对关键点缺失与无依据扩写，强化逐证据作答和下一步行动建议。",
            "",
            "### 评测集",
            "",
            "持续补齐部分支持和未定位题目的黄金证据，并用人工复核校准裁判。",
            "",
            "## 限制说明",
            "",
            "全部题目均纳入保守指标；评测基础设施失败另行统计，不伪造语义分数。",
        ]
    )
    if bool(run_info.get("同模型裁判")):
        lines.append("本次使用同模型裁判，可能存在自评偏差，结论应结合人工抽检。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def safe_backfill(
    source: Path,
    run_dir: Path,
    adapter: WorkbookBackfillAdapter,
    payload: Mapping[str, object],
) -> Path:
    source = Path(source)
    run_dir = Path(run_dir)
    if not source.is_file():
        raise ValueError(f"评测工作簿不存在：{source}")
    run_dir.mkdir(parents=True, exist_ok=True)
    backup = run_dir / "input-backup.xlsx"
    snapshot = run_dir / "工作簿快照.json"
    payload_path = run_dir / "工作簿回填.json"
    candidate = run_dir / "待验证.xlsx"
    verification = run_dir / "工作簿验证.json"
    preview_dir = run_dir / "预览"

    shutil.copy2(source, backup)
    try:
        adapter.extract(backup, snapshot)
        payload_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        adapter.backfill(backup, payload_path, candidate)
    except Exception as exc:
        raise ValueError(f"工作簿回填失败：{exc}") from exc

    try:
        adapter.verify(candidate, snapshot, verification, preview_dir)
        verification_payload = json.loads(verification.read_text(encoding="utf-8"))
        if not isinstance(verification_payload, dict) or not verification_payload.get(
            "验证通过"
        ):
            raise ValueError("验证结果未通过")
    except Exception as exc:
        raise ValueError(f"工作簿验证失败：{exc}") from exc

    os.replace(candidate, source)
    _fsync_directory(source.parent)
    return verification


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(os.fspath(path), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _integer(value: object) -> int:
    return int(_number(value))


def _ratio_text(value: object) -> str:
    return f"{_number(value):.1%}"


def _yes_no(value: object) -> str:
    return "是" if value is True else "否"
