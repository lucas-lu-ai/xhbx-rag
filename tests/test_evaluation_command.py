from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from xhbx_rag import cli
from xhbx_rag.evaluation.command import run_evaluate_command, select_items
from xhbx_rag.evaluation.models import EvaluationItem


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
