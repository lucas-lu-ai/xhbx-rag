from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Protocol
from uuid import uuid4

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


class WorkbookValidationError(ValueError):
    """工作簿输入、竞态或验证结果不满足安全回填条件。"""


class WorkbookPersistenceError(OSError):
    """工作簿快照或回填结果无法耐久落盘。"""


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
    gold_metrics_measured = (
        deterministic is not None and result.trace_status != "未定位"
    )
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
            ("是" if deterministic.primary_chunk_hit else "否")
            if gold_metrics_measured and deterministic is not None
            else None
        ),
        "黄金chunk召回率": (
            deterministic.gold_chunk_recall
            if gold_metrics_measured and deterministic is not None
            else None
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


def create_input_snapshot(
    source: Path,
    run_dir: Path,
    *,
    resume: bool = False,
    lock_root: Path | None = None,
) -> tuple[Path, str]:
    """创建一次性、耐久的输入快照；续跑只读取既有快照。"""
    source = Path(source)
    run_dir = Path(run_dir)
    backup = run_dir / "input-backup.xlsx"
    if resume:
        if not backup.is_file():
            raise WorkbookValidationError("断点续跑缺少输入快照")
        try:
            return backup, _sha256_file(backup)
        except OSError as exc:
            raise WorkbookPersistenceError("读取输入快照失败") from exc
    if backup.exists():
        raise WorkbookValidationError("输入快照已存在，不能静默覆盖")
    if not source.is_file():
        raise WorkbookValidationError(f"评测工作簿不存在：{source}")

    lock_directory = lock_root or run_dir.parent / ".workbook-locks"
    try:
        _ensure_directory_durable(run_dir)
        with _workbook_source_lock(source, lock_directory):
            temporary = run_dir / f".input-backup.{uuid4().hex}.tmp"
            try:
                _copy_file_and_fsync(source, temporary)
                os.link(temporary, backup)
                _fsync_directory(run_dir)
            finally:
                temporary.unlink(missing_ok=True)
                _fsync_directory(run_dir)
        return backup, _sha256_file(backup)
    except WorkbookValidationError:
        raise
    except OSError as exc:
        raise WorkbookPersistenceError("创建输入快照失败") from exc


def install_dataset_snapshot(
    staged_dataset: Path,
    installed_dataset: Path,
    *,
    resume: bool,
) -> Path:
    """在指纹验证后安装新数据集，或验证续跑数据集未变化。"""
    staged_dataset = Path(staged_dataset)
    installed_dataset = Path(installed_dataset)
    if not staged_dataset.is_file():
        raise WorkbookValidationError("临时dataset.json不存在")
    try:
        if resume:
            if not installed_dataset.is_file():
                raise WorkbookValidationError(
                    "断点续跑缺少既有dataset.json"
                )
            if _sha256_file(staged_dataset) != _sha256_file(installed_dataset):
                raise WorkbookValidationError(
                    "断点续跑的dataset.json不一致"
                )
            return installed_dataset
        if installed_dataset.exists():
            raise WorkbookValidationError(
                "运行目录已有dataset.json，不能静默覆盖"
            )
        _fsync_file(staged_dataset)
        _fsync_directory(staged_dataset.parent)
        try:
            os.link(staged_dataset, installed_dataset)
        except FileExistsError as exc:
            raise WorkbookValidationError(
                "运行目录已有dataset.json，不能静默覆盖"
            ) from exc
        _fsync_directory(installed_dataset.parent)
        return installed_dataset
    except WorkbookValidationError:
        raise
    except OSError as exc:
        raise WorkbookPersistenceError("dataset.json耐久落盘失败") from exc


def safe_backfill(
    source: Path,
    run_dir: Path,
    adapter: WorkbookBackfillAdapter,
    payload: Mapping[str, object],
    *,
    expected_source_sha256: str,
    lock_root: Path | None = None,
) -> Path:
    source = Path(source)
    run_dir = Path(run_dir)
    backup = run_dir / "input-backup.xlsx"
    snapshot = run_dir / "工作簿快照.json"
    payload_path = run_dir / "工作簿回填.json"
    candidate = run_dir / "待验证.xlsx"
    verification = run_dir / "工作簿验证.json"
    preview_dir = run_dir / "预览"
    lock_directory = lock_root or run_dir.parent / ".workbook-locks"

    normalized_source_sha256 = _validate_expected_source(
        source,
        backup,
        expected_source_sha256,
    )
    try:
        adapter.extract(backup, snapshot)
        snapshot_payload = json.loads(snapshot.read_text(encoding="utf-8"))
        if not isinstance(snapshot_payload, dict):
            raise WorkbookValidationError("工作簿快照不是对象")
        normalized_payload = json.loads(
            json.dumps(payload, ensure_ascii=False, allow_nan=False)
        )
        snapshot_payload["回填载荷"] = normalized_payload
        _atomic_write_json(snapshot, snapshot_payload)
        _atomic_write_json(payload_path, normalized_payload)
        adapter.backfill(backup, payload_path, candidate)
        if not candidate.is_file():
            raise WorkbookValidationError("工作簿回填未生成候选文件")
        _fsync_file(candidate)
        _fsync_directory(candidate.parent)
    except WorkbookValidationError:
        raise
    except OSError as exc:
        raise WorkbookPersistenceError(f"工作簿回填落盘失败：{exc}") from exc
    except Exception as exc:
        raise WorkbookValidationError(f"工作簿回填失败：{exc}") from exc

    try:
        adapter.verify(candidate, snapshot, verification, preview_dir)
        verification_payload = json.loads(verification.read_text(encoding="utf-8"))
        if (
            not isinstance(verification_payload, dict)
            or verification_payload.get("验证通过") is not True
        ):
            raise WorkbookValidationError("验证结果未通过")
    except WorkbookValidationError as exc:
        raise WorkbookValidationError(f"工作簿验证失败：{exc}") from exc
    except OSError as exc:
        raise WorkbookPersistenceError(f"工作簿验证结果读取失败：{exc}") from exc
    except Exception as exc:
        raise WorkbookValidationError(f"工作簿验证失败：{exc}") from exc

    try:
        with _workbook_source_lock(source, lock_directory):
            try:
                current_source_sha256 = _sha256_file(source)
            except FileNotFoundError as exc:
                raise WorkbookValidationError(
                    "源工作簿在评测期间发生变化，已拒绝覆盖"
                ) from exc
            if current_source_sha256 != normalized_source_sha256:
                raise WorkbookValidationError(
                    "源工作簿在评测期间发生变化，已拒绝覆盖"
                )
            try:
                os.replace(candidate, source)
                _fsync_directory(source.parent)
            except OSError as exc:
                if candidate.exists():
                    raise WorkbookPersistenceError(
                        f"替换源工作簿失败：{exc}"
                    ) from exc
                rollback_error = _rollback_source(backup, source)
                if rollback_error is None:
                    raise WorkbookPersistenceError(
                        "源工作簿替换后的目录同步失败，已回滚至输入快照"
                    ) from exc
                raise WorkbookPersistenceError(
                    "源工作簿替换后的目录同步失败，回滚也未能耐久完成："
                    f"{type(rollback_error).__name__}"
                ) from exc
    except WorkbookValidationError:
        raise
    except WorkbookPersistenceError:
        raise
    except OSError as exc:
        raise WorkbookPersistenceError(f"工作簿并发锁或替换失败：{exc}") from exc
    return verification


def _validate_expected_source(
    source: Path,
    backup: Path,
    expected_source_sha256: str,
) -> str:
    if not source.is_file():
        raise WorkbookValidationError(f"评测工作簿不存在：{source}")
    if not backup.is_file():
        raise WorkbookValidationError("运行目录缺少输入快照")
    normalized = str(expected_source_sha256).strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise WorkbookValidationError("输入SHA256格式无效")
    try:
        if _sha256_file(backup) != normalized:
            raise WorkbookValidationError("输入快照与初始SHA256不一致")
    except OSError as exc:
        raise WorkbookPersistenceError("读取输入快照失败") from exc
    return normalized


@contextmanager
def _workbook_source_lock(source: Path, lock_root: Path) -> Iterator[None]:
    _ensure_directory_durable(lock_root)
    source_key = hashlib.sha256(
        os.fspath(source.resolve()).encode("utf-8")
    ).hexdigest()
    lock_path = lock_root / f"{source_key}.lock"
    existed = lock_path.exists()
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if not existed:
            os.fsync(descriptor)
            _fsync_directory(lock_root)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _atomic_write_json(path: Path, payload: object) -> None:
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_file_and_fsync(source: Path, target: Path) -> None:
    with source.open("rb") as input_stream, target.open("xb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream)
        output_stream.flush()
        os.fsync(output_stream.fileno())


def _fsync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rollback_source(backup: Path, source: Path) -> OSError | None:
    temporary = source.parent / f".{source.name}.rollback.{uuid4().hex}.tmp"
    try:
        _copy_file_and_fsync(backup, temporary)
        _fsync_directory(source.parent)
        os.replace(temporary, source)
        _fsync_directory(source.parent)
    except OSError as exc:
        return exc
    finally:
        temporary.unlink(missing_ok=True)
    return None


def _ensure_directory_durable(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    path.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
        _fsync_directory(created.parent)


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
