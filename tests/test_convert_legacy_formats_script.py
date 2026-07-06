import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "convert_legacy_formats.py"
_spec = importlib.util.spec_from_file_location("convert_legacy_formats", _SCRIPT_PATH)
convert_legacy_formats = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("convert_legacy_formats", convert_legacy_formats)
_spec.loader.exec_module(convert_legacy_formats)


def test_build_conversion_plans_targets_legacy_formats(tmp_path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "教材.doc").write_bytes(b"doc")
    (tmp_path / "a" / "课件.ppt").write_bytes(b"ppt")
    (tmp_path / "a" / "手册.wps").write_bytes(b"wps")
    (tmp_path / "a" / "新课件.pptx").write_bytes(b"pptx")
    (tmp_path / "a" / "~$教材.doc").write_bytes(b"tmp")

    plans, skipped = convert_legacy_formats.build_conversion_plans(tmp_path)

    assert {(plan.source.name, plan.target.name) for plan in plans} == {
        ("教材.doc", "教材.docx"),
        ("手册.wps", "手册.docx"),
        ("课件.ppt", "课件.pptx"),
    }
    assert skipped == []


def test_build_conversion_plans_skips_when_target_exists(tmp_path) -> None:
    (tmp_path / "教材.doc").write_bytes(b"doc")
    (tmp_path / "教材.docx").write_bytes(b"docx")

    plans, skipped = convert_legacy_formats.build_conversion_plans(tmp_path)

    assert plans == []
    assert len(skipped) == 1


def test_main_dry_run_prints_plan_without_converting(tmp_path, capsys) -> None:
    (tmp_path / "教材.doc").write_bytes(b"doc")

    exit_code = convert_legacy_formats.main(["--dir", str(tmp_path), "--dry-run"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "[计划]" in output
    assert not (tmp_path / "教材.docx").exists()
