from __future__ import annotations

import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from xhbx_rag.evaluation.models import (
    DeterministicScores,
    EvaluationResult,
    JudgeResult,
)
from xhbx_rag.evaluation.reporting import (
    WorkbookPersistenceError,
    WorkbookValidationError,
    build_backfill_payload,
    create_input_snapshot,
    install_dataset_snapshot,
    safe_backfill,
    write_markdown_report,
)


def _hold_source_lock(
    source: Path,
    lock_root: Path,
    acquired: object,
    release: object,
) -> None:
    from xhbx_rag.evaluation.reporting import _workbook_source_lock

    with _workbook_source_lock(source, lock_root):
        acquired.set()  # type: ignore[attr-defined]
        release.wait(5)  # type: ignore[attr-defined]


def _mark_after_source_lock(
    source: Path,
    lock_root: Path,
    acquired: object,
) -> None:
    from xhbx_rag.evaluation.reporting import _workbook_source_lock

    with _workbook_source_lock(source, lock_root):
        acquired.set()  # type: ignore[attr-defined]


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


def test_backfill_payload_leaves_unmeasured_retrieval_metrics_blank() -> None:
    failed = EvaluationResult(
        item_id="row-3",
        excel_row=3,
        question="如何沟通预算？",
        reference_answer="先确认预算。",
        trace_status="未定位",
        answer="",
        answer_response={},
        duration_seconds=1.2,
        deterministic_scores=None,
        judge_result=None,
        total_score=0,
        grade="问答失败",
        status="问答失败",
        error_tags=["问答执行失败"],
        error_summary="问答执行失败，请稍后重试",
    )

    row = build_backfill_payload(_run_info(), _summary(), [failed])["逐题结果"][0]

    assert row["主chunk命中"] is None
    assert row["黄金chunk召回率"] is None


def test_backfill_payload_leaves_unlocated_gold_metrics_blank() -> None:
    unlocated = _result().model_copy(update={"trace_status": "未定位"})

    row = build_backfill_payload(_run_info(), _summary(), [unlocated])["逐题结果"][0]

    assert row["主chunk命中"] is None
    assert row["黄金chunk召回率"] is None


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
    _, input_sha256 = create_input_snapshot(source, run_dir)

    with pytest.raises(WorkbookValidationError, match="工作簿验证失败"):
        safe_backfill(
            source,
            run_dir,
            _FailingVerifyAdapter(),
            {"运行信息": {}, "汇总指标": {}, "逐题结果": []},
            expected_source_sha256=input_sha256,
        )

    assert source.read_bytes() == b"original"
    assert (run_dir / "input-backup.xlsx").read_bytes() == b"original"
    assert (run_dir / "待验证.xlsx").read_bytes() == b"candidate"


class _SuccessfulAdapter:
    def __init__(
        self,
        run_dir: Path,
        *,
        candidate: bytes = b"candidate",
        barrier: Barrier | None = None,
        mutate_source: Path | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.candidate = candidate
        self.barrier = barrier
        self.mutate_source = mutate_source

    def extract(self, input_path: Path, output_path: Path) -> Path:
        assert input_path == self.run_dir / "input-backup.xlsx"
        output_path.write_text('{"工作簿快照": {}}\n', encoding="utf-8")
        return output_path

    def backfill(
        self,
        input_path: Path,
        payload_path: Path,
        output_path: Path,
    ) -> Path:
        assert input_path == self.run_dir / "input-backup.xlsx"
        assert json.loads(payload_path.read_text(encoding="utf-8"))["逐题结果"] == []
        snapshot = json.loads(
            (self.run_dir / "工作簿快照.json").read_text(encoding="utf-8")
        )
        assert snapshot["回填载荷"]["逐题结果"] == []
        output_path.write_bytes(self.candidate)
        return output_path

    def verify(
        self,
        input_path: Path,
        snapshot_path: Path,
        output_path: Path,
        preview_dir: Path,
    ) -> Path:
        del input_path, snapshot_path, preview_dir
        output_path.write_text('{"验证通过": true}\n', encoding="utf-8")
        if self.mutate_source is not None:
            self.mutate_source.write_bytes(b"external-edit")
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        return output_path


class _TruthyStringVerifyAdapter(_SuccessfulAdapter):
    def verify(
        self,
        input_path: Path,
        snapshot_path: Path,
        output_path: Path,
        preview_dir: Path,
    ) -> Path:
        del input_path, snapshot_path, preview_dir
        output_path.write_text('{"验证通过": "是"}\n', encoding="utf-8")
        return output_path


class _DeletingSourceAdapter(_SuccessfulAdapter):
    def __init__(self, run_dir: Path, source: Path) -> None:
        super().__init__(run_dir)
        self.source = source

    def verify(
        self,
        input_path: Path,
        snapshot_path: Path,
        output_path: Path,
        preview_dir: Path,
    ) -> Path:
        result = super().verify(
            input_path,
            snapshot_path,
            output_path,
            preview_dir,
        )
        self.source.unlink()
        return result


def test_create_input_snapshot_is_reused_without_overwriting_on_resume(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"original")
    run_dir = tmp_path / "run"

    backup, digest = create_input_snapshot(source, run_dir)
    source.write_bytes(b"changed")
    resumed_backup, resumed_digest = create_input_snapshot(
        source,
        run_dir,
        resume=True,
    )

    assert backup == resumed_backup
    assert backup.read_bytes() == b"original"
    assert resumed_digest == digest
    with pytest.raises(WorkbookValidationError, match="输入快照已存在"):
        create_input_snapshot(source, run_dir)


def test_install_dataset_snapshot_rejects_changed_resume_dataset(
    tmp_path: Path,
) -> None:
    staged = tmp_path / "staged.json"
    staged.write_bytes(b"new-dataset")
    installed = tmp_path / "run" / "dataset.json"
    installed.parent.mkdir()
    installed.write_bytes(b"existing-dataset")

    with pytest.raises(WorkbookValidationError, match="dataset.json不一致"):
        install_dataset_snapshot(staged, installed, resume=True)

    assert installed.read_bytes() == b"existing-dataset"
    assert staged.read_bytes() == b"new-dataset"


def test_install_dataset_snapshot_atomically_installs_new_dataset(
    tmp_path: Path,
) -> None:
    staged = tmp_path / "staged.json"
    staged.write_bytes(b"new-dataset")
    installed = tmp_path / "run" / "dataset.json"
    installed.parent.mkdir()

    install_dataset_snapshot(staged, installed, resume=False)

    assert installed.read_bytes() == b"new-dataset"


def test_safe_backfill_embeds_payload_in_snapshot_and_removes_json_temp_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"original")
    run_dir = tmp_path / "run"
    _, digest = create_input_snapshot(source, run_dir)
    payload = {"运行信息": {}, "汇总指标": {}, "逐题结果": []}

    safe_backfill(
        source,
        run_dir,
        _SuccessfulAdapter(run_dir),
        payload,
        expected_source_sha256=digest,
    )

    snapshot = json.loads((run_dir / "工作簿快照.json").read_text("utf-8"))
    assert snapshot["回填载荷"] == payload
    assert not list(run_dir.glob("*.tmp"))


def test_safe_backfill_requires_literal_true_verification_result(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"original")
    run_dir = tmp_path / "run"
    _, digest = create_input_snapshot(source, run_dir)

    with pytest.raises(WorkbookValidationError, match="验证结果未通过"):
        safe_backfill(
            source,
            run_dir,
            _TruthyStringVerifyAdapter(run_dir),
            {"运行信息": {}, "汇总指标": {}, "逐题结果": []},
            expected_source_sha256=digest,
        )

    assert source.read_bytes() == b"original"


def test_safe_backfill_rejects_source_changed_during_evaluation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"original")
    run_dir = tmp_path / "run"
    _, digest = create_input_snapshot(source, run_dir)

    with pytest.raises(WorkbookValidationError, match="评测期间发生变化"):
        safe_backfill(
            source,
            run_dir,
            _SuccessfulAdapter(run_dir, mutate_source=source),
            {"运行信息": {}, "汇总指标": {}, "逐题结果": []},
            expected_source_sha256=digest,
        )

    assert source.read_bytes() == b"external-edit"
    assert (run_dir / "input-backup.xlsx").read_bytes() == b"original"


def test_safe_backfill_treats_deleted_source_as_validation_race(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"original")
    run_dir = tmp_path / "run"
    _, digest = create_input_snapshot(source, run_dir)

    with pytest.raises(WorkbookValidationError, match="评测期间发生变化"):
        safe_backfill(
            source,
            run_dir,
            _DeletingSourceAdapter(run_dir, source),
            {"运行信息": {}, "汇总指标": {}, "逐题结果": []},
            expected_source_sha256=digest,
        )

    assert not source.exists()


def test_safe_backfill_serializes_concurrent_writers_with_stable_lock(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"original")
    barrier = Barrier(2)
    payload = {"运行信息": {}, "汇总指标": {}, "逐题结果": []}
    attempts: list[tuple[Path, str, bytes]] = []
    for index in (1, 2):
        run_dir = tmp_path / f"run-{index}"
        _, digest = create_input_snapshot(source, run_dir)
        attempts.append((run_dir, digest, f"candidate-{index}".encode()))

    def run_attempt(attempt: tuple[Path, str, bytes]) -> str:
        run_dir, digest, candidate = attempt
        try:
            safe_backfill(
                source,
                run_dir,
                _SuccessfulAdapter(
                    run_dir,
                    candidate=candidate,
                    barrier=barrier,
                ),
                payload,
                expected_source_sha256=digest,
                lock_root=tmp_path / "locks",
            )
        except WorkbookValidationError:
            return "已拒绝"
        return "已回填"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(run_attempt, attempts))

    assert sorted(outcomes) == ["已回填", "已拒绝"]
    assert source.read_bytes() in {b"candidate-1", b"candidate-2"}


def test_workbook_sidecar_lock_is_mutually_exclusive_across_processes(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"original")
    lock_root = tmp_path / "locks"
    first_acquired = context.Event()
    release_first = context.Event()
    second_acquired = context.Event()
    first = context.Process(
        target=_hold_source_lock,
        args=(source, lock_root, first_acquired, release_first),
    )
    second = context.Process(
        target=_mark_after_source_lock,
        args=(source, lock_root, second_acquired),
    )
    first.start()
    try:
        assert first_acquired.wait(5)
        second.start()
        assert not second_acquired.wait(0.2)
        release_first.set()
        assert second_acquired.wait(5)
    finally:
        release_first.set()
        first.join(5)
        if second.pid is not None:
            second.join(5)
        if first.is_alive():
            first.terminate()
            first.join(5)
        if second.pid is not None and second.is_alive():
            second.terminate()
            second.join(5)

    assert first.exitcode == 0
    assert second.exitcode == 0


def test_safe_backfill_rolls_source_back_when_final_directory_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xhbx_rag.evaluation.reporting as reporting

    source = tmp_path / "source.xlsx"
    source.write_bytes(b"original")
    run_dir = tmp_path / "run"
    _, digest = create_input_snapshot(source, run_dir)
    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    original_fsync_directory = reporting._fsync_directory
    source_sync_calls = 0

    def fail_first_source_sync(path: Path) -> None:
        nonlocal source_sync_calls
        if Path(path) == source.parent:
            source_sync_calls += 1
            if source_sync_calls == 1:
                raise OSError("模拟目录 fsync 失败")
        original_fsync_directory(path)

    monkeypatch.setattr(reporting, "_fsync_directory", fail_first_source_sync)

    with pytest.raises(WorkbookPersistenceError, match="已回滚"):
        safe_backfill(
            source,
            run_dir,
            _SuccessfulAdapter(run_dir),
            {"运行信息": {}, "汇总指标": {}, "逐题结果": []},
            expected_source_sha256=digest,
            lock_root=lock_root,
        )

    assert source_sync_calls == 3
    assert source.read_bytes() == b"original"
    assert (run_dir / "input-backup.xlsx").read_bytes() == b"original"
