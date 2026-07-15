from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from xhbx_rag import cli
from xhbx_rag.evaluation.command import run_evaluate_command, select_items
from xhbx_rag.evaluation.models import EvaluationItem
from xhbx_rag.evaluation.reporting import WorkbookPersistenceError


def _args(tmp_path: Path, **overrides: object) -> SimpleNamespace:
    values = {
        "dataset": tmp_path / "input.xlsx",
        "output_dir": tmp_path / "out",
        "concurrency": 2,
        "judge_concurrency": 2,
        "top_n": 20,
        "top_k": 5,
        "limit": None,
        "item_id": None,
        "resume": None,
        "no_xlsx": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _item(excel_row: int) -> EvaluationItem:
    return EvaluationItem(
        item_id=f"row-{excel_row}",
        excel_row=excel_row,
        question=f"问题{excel_row}",
        reference_answer="参考答案",
        trace_status="未定位",
    )


def test_cli_registers_evaluate_command(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(args: object) -> int:
        observed.update(vars(args))
        return 0

    monkeypatch.setattr(cli, "run_evaluate_command", fake_run)

    assert (
        cli.main(
            [
                "evaluate",
                "--dataset",
                "input.xlsx",
                "--output-dir",
                "out",
            ]
        )
        == 0
    )
    assert observed == {
        "command": "evaluate",
        "dataset": Path("input.xlsx"),
        "output_dir": Path("out"),
        "concurrency": 2,
        "judge_concurrency": 2,
        "top_n": 20,
        "top_k": 5,
        "limit": None,
        "item_id": None,
        "resume": None,
        "no_xlsx": False,
    }


def test_cli_evaluate_supports_selection_resume_and_no_xlsx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(args: object) -> int:
        observed.update(vars(args))
        return 0

    monkeypatch.setattr(cli, "run_evaluate_command", fake_run)

    assert (
        cli.main(
            [
                "evaluate",
                "--dataset",
                "input.xlsx",
                "--limit",
                "3",
                "--item-id",
                "row-2",
                "--item-id",
                "row-4",
                "--resume",
                "run-1",
                "--no-xlsx",
            ]
        )
        == 0
    )
    assert observed["limit"] == 3
    assert observed["item_id"] == ["row-2", "row-4"]
    assert observed["resume"] == "run-1"
    assert observed["no_xlsx"] is True


def test_cli_returns_evaluation_command_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "run_evaluate_command", lambda _args: 3)

    assert cli.main(["evaluate", "--dataset", "input.xlsx"]) == 3


def test_select_items_applies_item_ids_then_limit() -> None:
    selected = select_items(
        [_item(2), _item(3), _item(4), _item(5)],
        item_ids=["row-5", "row-3", "row-4"],
        limit=2,
    )

    assert [item.item_id for item in selected] == ["row-3", "row-4"]


@pytest.mark.parametrize("limit", [0, -1, True])
def test_select_items_rejects_non_positive_integer_limit(limit: object) -> None:
    with pytest.raises(ValueError, match="limit 必须是正整数"):
        select_items([_item(2)], item_ids=None, limit=limit)


def test_select_items_rejects_unknown_ids() -> None:
    with pytest.raises(ValueError, match="评测项ID不存在：row-99"):
        select_items([_item(2)], item_ids=["row-99"], limit=None)


def test_run_evaluate_command_returns_two_for_missing_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = run_evaluate_command(_args(tmp_path))

    assert exit_code == 2
    assert "评测输入失败" in capsys.readouterr().err


def test_run_evaluate_command_returns_two_for_invalid_cli_limits(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = tmp_path / "input.xlsx"
    dataset.write_bytes(b"not-an-xlsx")

    exit_code = run_evaluate_command(_args(tmp_path, top_n=0))

    assert exit_code == 2
    assert "评测输入失败" in capsys.readouterr().err


def test_resume_configuration_failure_does_not_replace_existing_dataset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import xhbx_rag.evaluation.command as command

    source = tmp_path / "input.xlsx"
    source.write_bytes(b"source")
    run_dir = tmp_path / "out" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "input-backup.xlsx").write_bytes(b"source")
    dataset_json = run_dir / "dataset.json"
    dataset_json.write_bytes(b"existing-dataset-fact")

    class FakeAdapter:
        def __init__(self, adapter_dir: Path) -> None:
            self.adapter_dir = adapter_dir

        def extract(self, input_path: Path, output_path: Path) -> Path:
            assert input_path == run_dir / "input-backup.xlsx"
            assert output_path != dataset_json
            output_path.write_bytes(b"newly-extracted-dataset")
            return output_path

    monkeypatch.setattr(command, "WorkbookAdapter", FakeAdapter)
    monkeypatch.setattr(command, "load_dataset", lambda _path: [_item(2)])

    def fail_config() -> object:
        raise command.ConfigError("模拟配置失败")

    monkeypatch.setattr(command.RetrievalConfig, "from_env", fail_config)

    exit_code = run_evaluate_command(_args(tmp_path, resume="run-1"))

    assert exit_code == 2
    assert dataset_json.read_bytes() == b"existing-dataset-fact"
    assert "评测输入失败" in capsys.readouterr().err


def test_resume_fingerprint_rejection_happens_before_dataset_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xhbx_rag.evaluation.command as command

    source = tmp_path / "input.xlsx"
    source.write_bytes(b"source")
    run_dir = tmp_path / "out" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "input-backup.xlsx").write_bytes(b"source")
    dataset_json = run_dir / "dataset.json"
    dataset_json.write_bytes(b"existing-dataset-fact")

    class FakeAdapter:
        def __init__(self, adapter_dir: Path) -> None:
            self.adapter_dir = adapter_dir

        def extract(self, input_path: Path, output_path: Path) -> Path:
            assert input_path == run_dir / "input-backup.xlsx"
            output_path.write_bytes(b"newly-extracted-dataset")
            return output_path

    retrieval_config = SimpleNamespace(
        model_name="answer-model",
        milvus_uri="http://localhost:19530",
    )
    judge_config = SimpleNamespace(
        judge_model_name="judge-model",
        same_model_judge=False,
    )
    monkeypatch.setattr(command, "WorkbookAdapter", FakeAdapter)
    monkeypatch.setattr(command, "load_dataset", lambda _path: [_item(2)])
    monkeypatch.setattr(
        command.RetrievalConfig,
        "from_env",
        lambda: retrieval_config,
    )
    monkeypatch.setattr(
        command,
        "load_evaluation_config",
        lambda: judge_config,
    )
    monkeypatch.setattr(
        command,
        "preflight_docker_milvus",
        lambda _config: {"案例知识库": {"存在": True, "数据量": 1}},
    )
    monkeypatch.setattr(
        command,
        "compute_run_fingerprint",
        lambda **_kwargs: "f" * 64,
    )

    def reject_resume(*_args: object, **_kwargs: object) -> object:
        raise ValueError("运行配置指纹不一致")

    monkeypatch.setattr(command, "validate_resume", reject_resume)
    monkeypatch.setattr(
        command,
        "install_dataset_snapshot",
        lambda *_args, **_kwargs: pytest.fail("不得在指纹验证失败后安装dataset"),
    )

    exit_code = run_evaluate_command(_args(tmp_path, resume="run-1"))

    assert exit_code == 2
    assert dataset_json.read_bytes() == b"existing-dataset-fact"


def test_run_evaluate_command_maps_snapshot_disk_full_to_exit_three(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import xhbx_rag.evaluation.command as command

    source = tmp_path / "input.xlsx"
    source.write_bytes(b"source")

    def fail_snapshot(*_args: object, **_kwargs: object) -> object:
        raise WorkbookPersistenceError("模拟磁盘已满")

    monkeypatch.setattr(command, "create_input_snapshot", fail_snapshot)

    exit_code = run_evaluate_command(_args(tmp_path))

    assert exit_code == 3
    assert "评测落盘失败" in capsys.readouterr().err
