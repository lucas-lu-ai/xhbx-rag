from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from hashlib import sha256
from pathlib import Path

import pytest

import xhbx_rag.evaluation.dataset as dataset_module
import xhbx_rag.evaluation.workbook as workbook_module
from xhbx_rag.evaluation.dataset import load_chunk_catalog, load_dataset
from xhbx_rag.evaluation.workbook import WorkbookAdapter


REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_WORKBOOK = REPO_ROOT / "docs" / "新华保险AI教练问答一批绩优案例测试集-含完整溯源.xlsx"
FIXED_NODE_BIN = Path(
    "/Users/milan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
)
FIXED_NODE_MODULES = Path(
    "/Users/milan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
)


def _evaluation_rows(*, excel_rows: list[int] | None = None) -> list[dict]:
    row_numbers = excel_rows or list(range(2, 52))
    return [
        {
            "评测项ID": f"row-{excel_row}",
            "Excel行号": excel_row,
            "问题": "如何沟通预算？",
            "参考答案": "先确认预算。",
            "溯源状态": "完整支持",
            "主chunk_id": "chunk-1",
            "黄金chunk_id列表": ["chunk-1"],
            "黄金证据": [
                {
                    "chunk_id": "chunk-1",
                    "原文摘录": "先确认预算",
                }
            ],
        }
        for excel_row in row_numbers
    ]


def _write_dataset(path: Path, rows: list[dict]) -> None:
    path.write_text(
        json.dumps({"评测项": rows}, ensure_ascii=False),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _recorded_node_calls(path: Path) -> list[list[str]]:
    calls: list[list[str]] = []
    current: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "--":
            calls.append(current)
            current = []
        else:
            current.append(line)
    assert current == []
    return calls


def _workbook_with_extra_main_row(tmp_path: Path) -> Path:
    runtime_dir = tmp_path / "fixture-runtime"
    runtime_dir.mkdir()
    (runtime_dir / "node_modules").symlink_to(
        FIXED_NODE_MODULES,
        target_is_directory=True,
    )
    output_path = tmp_path / "额外 第52行.xlsx"
    script = """
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
const input = await FileBlob.load(process.env.EVALUATION_TEST_INPUT);
const workbook = await SpreadsheetFile.importXlsx(input);
const sheet = workbook.worksheets.getItem("绩优案例测试-楚琦");
sheet.getRange("A52:E52").values = [[
  "不应被忽略的额外问题",
  "不应被忽略的额外参考答案",
  "未定位",
  "",
  "边界测试",
]];
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(process.env.EVALUATION_TEST_OUTPUT);
"""
    env = os.environ.copy()
    env.update(
        {
            "EVALUATION_TEST_INPUT": str(REAL_WORKBOOK),
            "EVALUATION_TEST_OUTPUT": str(output_path),
        }
    )
    subprocess.run(
        [str(FIXED_NODE_BIN), "--input-type=module", "--eval", script],
        cwd=runtime_dir,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return output_path


def test_load_dataset_accepts_chinese_contract_and_sorts_excel_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dataset.json"
    _write_dataset(path, list(reversed(_evaluation_rows())))

    items = load_dataset(path)

    assert len(items) == 50
    assert items[0].excel_row == 2
    assert items[-1].excel_row == 51
    assert items[0].gold_chunk_ids == ["chunk-1"]


def test_load_dataset_rejects_non_fifty_item_dataset(tmp_path: Path) -> None:
    path = tmp_path / "dataset.json"
    _write_dataset(path, _evaluation_rows()[:-1])

    with pytest.raises(ValueError, match="评测集必须包含 50 条评测项"):
        load_dataset(path)


def test_load_dataset_rejects_duplicate_excel_rows(tmp_path: Path) -> None:
    rows = _evaluation_rows(excel_rows=[2] * 50)
    path = tmp_path / "dataset.json"
    _write_dataset(path, rows)

    with pytest.raises(ValueError, match="Excel行号重复"):
        load_dataset(path)


def test_load_dataset_reports_duplicate_and_missing_excel_rows(
    tmp_path: Path,
) -> None:
    excel_rows = list(range(2, 52))
    excel_rows[-1] = 50
    path = tmp_path / "dataset.json"
    _write_dataset(path, _evaluation_rows(excel_rows=excel_rows))

    with pytest.raises(
        ValueError,
        match=(
            "Excel行号重复：50；Excel行号必须恰好为 2\\.\\.51；"
            "缺失：51；越界：无"
        ),
    ):
        load_dataset(path)


@pytest.mark.parametrize("outside_row", [1, 52])
def test_load_dataset_rejects_missing_and_out_of_range_excel_rows_in_chinese(
    tmp_path: Path,
    outside_row: int,
) -> None:
    excel_rows = list(range(2, 52))
    excel_rows[0] = outside_row
    path = tmp_path / "dataset.json"
    _write_dataset(path, _evaluation_rows(excel_rows=excel_rows))

    with pytest.raises(
        ValueError,
        match=rf"Excel行号必须恰好为 2\.\.51；缺失：2；越界：{outside_row}",
    ):
        load_dataset(path)


def test_load_dataset_rejects_item_id_that_does_not_match_excel_row(
    tmp_path: Path,
) -> None:
    rows = _evaluation_rows()
    rows[0]["评测项ID"] = "row-wrong"
    path = tmp_path / "dataset.json"
    _write_dataset(path, rows)

    with pytest.raises(
        ValueError,
        match="评测项ID与Excel行号不一致：Excel行号 2 应为 row-2，实际为 row-wrong",
    ):
        load_dataset(path)


def test_load_chunk_catalog_reads_nested_chunks_and_deduplicates(
    tmp_path: Path,
) -> None:
    first = tmp_path / "案例" / "chunks.jsonl"
    second = tmp_path / "课程" / "嵌套" / "chunks.jsonl"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text(
        '\n'.join(
            [
                json.dumps({"chunk_id": "chunk-1"}, ensure_ascii=False),
                json.dumps({"chunk_id": "chunk-shared"}, ensure_ascii=False),
                "",
            ]
        ),
        encoding="utf-8",
    )
    second.write_text(
        '\n'.join(
            [
                json.dumps({"chunk_id": "chunk-shared"}, ensure_ascii=False),
                json.dumps({"chunk_id": " chunk-2 "}, ensure_ascii=False),
            ]
        ),
        encoding="utf-8",
    )

    assert load_chunk_catalog(tmp_path) == {
        "chunk-1",
        "chunk-2",
        "chunk-shared",
    }


def test_load_chunk_catalog_defaults_to_docs_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks_path = tmp_path / "docs" / "案例" / "chunks.jsonl"
    chunks_path.parent.mkdir(parents=True)
    chunks_path.write_text('{"chunk_id":"docs-chunk"}\n', encoding="utf-8")
    monkeypatch.setattr(dataset_module, "DEFAULT_PARSED_ROOT", tmp_path / "docs")

    assert load_chunk_catalog() == {"docs-chunk"}


def test_workbook_adapter_requires_bundled_runtime_configuration(
    tmp_path: Path,
) -> None:
    adapter = WorkbookAdapter(tmp_path, env={})

    with pytest.raises(RuntimeError, match="未配置 EVALUATION_NODE_BIN"):
        adapter.extract(tmp_path / "input.xlsx", tmp_path / "dataset.json")


def test_workbook_adapter_wraps_script_copy_oserror_in_chinese(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_copy(_source: Path, _target: Path) -> None:
        raise OSError("模拟复制失败")

    monkeypatch.setattr(workbook_module.shutil, "copy2", fail_copy)
    adapter = WorkbookAdapter(
        tmp_path / "run",
        env={
            "EVALUATION_NODE_BIN": str(FIXED_NODE_BIN),
            "EVALUATION_ARTIFACT_NODE_MODULES": str(FIXED_NODE_MODULES),
        },
    )

    with pytest.raises(
        RuntimeError,
        match="复制工作簿脚本失败：.*evaluation_workbook.mjs.*模拟复制失败",
    ):
        adapter.extract(tmp_path / "input.xlsx", tmp_path / "dataset.json")


def test_workbook_adapter_wraps_node_modules_symlink_oserror_in_chinese(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_symlink(
        _link: Path,
        _target: Path,
        *,
        target_is_directory: bool = False,
    ) -> None:
        del target_is_directory
        raise OSError("模拟软链接失败")

    monkeypatch.setattr(Path, "symlink_to", fail_symlink)
    adapter = WorkbookAdapter(
        tmp_path / "run",
        env={
            "EVALUATION_NODE_BIN": str(FIXED_NODE_BIN),
            "EVALUATION_ARTIFACT_NODE_MODULES": str(FIXED_NODE_MODULES),
        },
    )

    with pytest.raises(
        RuntimeError,
        match="创建 node_modules 软链接失败：.*node_modules.*模拟软链接失败",
    ):
        adapter.extract(tmp_path / "input.xlsx", tmp_path / "dataset.json")


def test_workbook_adapter_wraps_subprocess_start_oserror_in_chinese(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_start(*_args: object, **_kwargs: object) -> None:
        raise OSError("模拟进程启动失败")

    monkeypatch.setattr(workbook_module.subprocess, "run", fail_start)
    adapter = WorkbookAdapter(
        tmp_path / "run",
        env={
            "EVALUATION_NODE_BIN": str(FIXED_NODE_BIN),
            "EVALUATION_ARTIFACT_NODE_MODULES": str(FIXED_NODE_MODULES),
        },
    )

    with pytest.raises(
        RuntimeError,
        match="启动 Node 工作簿进程失败：.*node.*模拟进程启动失败",
    ):
        adapter.extract(tmp_path / "input.xlsx", tmp_path / "dataset.json")


def test_workbook_adapter_resolves_relative_runtime_paths_before_changing_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_node = tmp_path / "relative-node"
    node_modules = tmp_path / "relative-node-modules"
    calls_path = tmp_path / "relative-calls.txt"
    node_modules.mkdir()
    fake_node.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$@" > "$EVALUATION_TEST_CALLS"\n',
        encoding="utf-8",
    )
    fake_node.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    adapter = WorkbookAdapter(
        tmp_path / "run",
        env={
            "EVALUATION_NODE_BIN": fake_node.name,
            "EVALUATION_ARTIFACT_NODE_MODULES": node_modules.name,
            "EVALUATION_TEST_CALLS": str(calls_path),
        },
    )

    adapter.extract(tmp_path / "input.xlsx", tmp_path / "dataset.json")

    assert calls_path.is_file()


def test_workbook_cli_reports_configuration_error_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("EVALUATION_NODE_BIN", raising=False)
    monkeypatch.delenv("EVALUATION_ARTIFACT_NODE_MODULES", raising=False)

    exit_code = workbook_module.main(
        [
            "extract",
            "--input",
            str(tmp_path / "input.xlsx"),
            "--output",
            str(tmp_path / "dataset.json"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "工作簿适配器错误：未配置 EVALUATION_NODE_BIN" in captured.err
    assert "Traceback" not in captured.err


def test_workbook_adapter_uses_configured_runtime_for_all_modes(
    tmp_path: Path,
) -> None:
    fake_node = tmp_path / "fixed-node"
    node_modules = tmp_path / "fixed-node-modules"
    calls_path = tmp_path / "calls.txt"
    node_modules.mkdir()
    fake_node.write_text(
        "#!/bin/sh\n"
        'for argument in "$@"; do printf \'%s\\n\' "$argument"; done '
        '>> "$EVALUATION_TEST_CALLS"\n'
        'printf \'%s\\n\' \'--\' >> "$EVALUATION_TEST_CALLS"\n',
        encoding="utf-8",
    )
    fake_node.chmod(0o755)
    env = {
        "EVALUATION_NODE_BIN": str(fake_node),
        "EVALUATION_ARTIFACT_NODE_MODULES": str(node_modules),
        "EVALUATION_TEST_CALLS": str(calls_path),
    }
    adapter = WorkbookAdapter(tmp_path / "run", env=env)
    input_path = tmp_path / "input.xlsx"
    dataset_path = tmp_path / "dataset.json"
    payload_path = tmp_path / "payload.json"
    output_path = tmp_path / "output.xlsx"
    snapshot_path = tmp_path / "snapshot.json"
    preview_dir = tmp_path / "previews"

    adapter.extract(input_path, dataset_path)
    adapter.backfill(
        input_path,
        payload_path,
        output_path,
    )
    adapter.verify(
        input_path,
        snapshot_path,
        output_path,
        preview_dir,
    )

    adapter_dir = tmp_path / "run" / ".workbook_adapter"
    copied_script = adapter_dir / "evaluation_workbook.mjs"
    linked_modules = adapter_dir / "node_modules"
    assert copied_script.read_bytes() == (
        REPO_ROOT / "scripts" / "evaluation_workbook.mjs"
    ).read_bytes()
    assert linked_modules.is_symlink()
    assert linked_modules.resolve() == node_modules.resolve()
    assert _recorded_node_calls(calls_path) == [
        [
            str(copied_script),
            "extract",
            "--input",
            str(input_path.resolve()),
            "--output",
            str(dataset_path.resolve()),
        ],
        [
            str(copied_script),
            "backfill",
            "--input",
            str(input_path.resolve()),
            "--payload",
            str(payload_path.resolve()),
            "--output",
            str(output_path.resolve()),
        ],
        [
            str(copied_script),
            "verify",
            "--input",
            str(input_path.resolve()),
            "--snapshot",
            str(snapshot_path.resolve()),
            "--output",
            str(output_path.resolve()),
            "--preview-dir",
            str(preview_dir.resolve()),
        ],
    ]


def test_workbook_adapter_extracts_real_workbook_without_modifying_source(
    tmp_path: Path,
) -> None:
    before_sha = _sha256(REAL_WORKBOOK)
    output_path = tmp_path / "dataset.json"
    adapter = WorkbookAdapter(
        tmp_path / "run",
        env={
            "EVALUATION_NODE_BIN": str(FIXED_NODE_BIN),
            "EVALUATION_ARTIFACT_NODE_MODULES": str(FIXED_NODE_MODULES),
        },
    )

    adapter.extract(REAL_WORKBOOK.relative_to(REPO_ROOT), output_path)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    items = load_dataset(output_path)
    assert len(items) == 50
    assert Counter(item.trace_status for item in items) == {
        "完整支持": 38,
        "部分支持": 11,
        "未定位": 1,
    }
    assert set(payload) == {"评测项"}
    assert set(payload["评测项"][0]) == {
        "评测项ID",
        "Excel行号",
        "问题",
        "参考答案",
        "溯源状态",
        "主chunk_id",
        "黄金chunk_id列表",
        "黄金证据",
    }
    assert [item.excel_row for item in items] == list(range(2, 52))

    first = payload["评测项"][0]
    first_chunk_id = (
        "蔡惠兰_营销就是彼此_看见_e1097ac860__strategy__"
        "线上个人品牌可视化经营策略_bb93750cf6"
    )
    assert first["评测项ID"] == "row-2"
    assert first["问题"] == "三点两面的系统经营步骤"
    assert first["参考答案"] == (
        "第一步\n线上打造自己的个人品牌\n让客户认识我\n"
        "第二步\n线下的客户服务体验经营\n让我去了解客户的魂\n"
        "第三步\n专业的学习提升\n赢得客户对我的认可"
    )
    assert first["主chunk_id"] == first_chunk_id
    assert first["黄金chunk_id列表"] == [first_chunk_id]
    assert len(first["黄金证据"]) == 2
    assert first["黄金证据"][0] == {
        "chunk_id": first_chunk_id,
        "来源路径": (
            "【蔡惠兰】营销就是彼此“看见”/第1节：线上打造品牌，展示自我/"
            "第1节：线上打造品牌，展示自我.docx"
        ),
        "来源定位": (
            "行88-88；课程主题：营销就是彼此看见——TOP培训助我突破百万 > "
            "3. 三点两面的系统经营思路 > 3.2 三个步骤"
        ),
        "原文摘录": (
            "| 第一步 | 线上打造自己的个人品牌 | 让客户认识我 |\n"
            "| 第二步 | 线下的客户服务体验经营 | 让我去了解客户的魂 |\n"
            "| 第三步 | 专业的学习提升 | 赢得客户对我的认可 |"
        ),
        "支撑说明": (
            "人工逐条复核确认该连续原文支撑 C1, C3, C4, C5, C6；"
            "支撑分为文本直接匹配分均值 1.0。"
        ),
    }
    assert first["黄金证据"][1]["chunk_id"] == first_chunk_id
    assert first["黄金证据"][1]["来源定位"] == "行214-223"
    assert first["黄金证据"][1]["原文摘录"] == "后来我觉得要让客户认识我"

    last = payload["评测项"][-1]
    assert last["评测项ID"] == "row-51"
    assert last["问题"] == "促成保单的关键要素是什么"
    assert sha256(last["参考答案"].encode("utf-8")).hexdigest() == (
        "b5bbb34083e3fba0788bc066d93b9167bd0857a304819651988ca51c1ef89045"
    )
    assert last["主chunk_id"] == (
        "周改娜_高效约见陌生高客_792ddaac9b__strategy__"
        "四问自检促成框架_8148381deb"
    )
    assert last["黄金chunk_id列表"] == [
        "周改娜_高效约见陌生高客_792ddaac9b__strategy__四问自检促成框架_8148381deb",
        (
            "周改娜_高效约见陌生高客_792ddaac9b__objection_handling__"
            "觉得保险没用_不需要_初始抗拒_c2d3a4a6ff"
        ),
        (
            "周改娜_高效约见陌生高客_792ddaac9b__objection_handling__"
            "等我有钱了再买_40f6acc9fe"
        ),
        (
            "周改娜_高效约见陌生高客_792ddaac9b__customer_journey__"
            "四问促成与缔结_ed35e0a84b"
        ),
        (
            "周改娜_高效约见陌生高客_792ddaac9b__strategy__"
            "平等专业心态建设策略_f6dc382647"
        ),
        (
            "周改娜_高效约见陌生高客_792ddaac9b__objection_handling__"
            "在公司买了单却没找我_8dfcf29666"
        ),
    ]
    assert len(last["黄金证据"]) == 13
    assert last["黄金证据"][-1] == {
        "chunk_id": (
            "周改娜_高效约见陌生高客_792ddaac9b__objection_handling__"
            "在公司买了单却没找我_8dfcf29666"
        ),
        "来源路径": "【周改娜】高效约见陌生高客/第4节四问促成/第4节四问促成.docx",
        "来源定位": "行177-177；课程主题：客户促成四问与实战案例 > 6. 核心要点速查",
        "原文摘录": "...你觉得我哪里做得不好，你帮我提升一下",
        "支撑说明": (
            "人工逐条复核确认该连续原文支撑 C39；"
            "支撑分为文本直接匹配分均值 1.0。"
        ),
    }

    unlocated = payload["评测项"][12]
    assert unlocated == {
        "评测项ID": "row-14",
        "Excel行号": 14,
        "问题": "转介绍客户需要收集哪些资料",
        "参考答案": (
            "收集内容：家庭结构、年龄收入结构、职业、现有保障（社保、商保、"
            "二次医疗报销）、父母保障情况、资产与负债（贷款、借债）。"
        ),
        "溯源状态": "未定位",
        "主chunk_id": "",
        "黄金chunk_id列表": [],
        "黄金证据": [],
    }
    assert _sha256(REAL_WORKBOOK) == before_sha


def test_workbook_adapter_extracts_to_missing_unicode_output_directory(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "中文 目录" / "评测 数据集.json"
    adapter = WorkbookAdapter(
        tmp_path / "run",
        env={
            "EVALUATION_NODE_BIN": str(FIXED_NODE_BIN),
            "EVALUATION_ARTIFACT_NODE_MODULES": str(FIXED_NODE_MODULES),
        },
    )

    adapter.extract(REAL_WORKBOOK, output_path)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(payload["评测项"]) == 50


def test_workbook_adapter_rejects_nonempty_main_row_52(tmp_path: Path) -> None:
    input_path = _workbook_with_extra_main_row(tmp_path)
    adapter = WorkbookAdapter(
        tmp_path / "run",
        env={
            "EVALUATION_NODE_BIN": str(FIXED_NODE_BIN),
            "EVALUATION_ARTIFACT_NODE_MODULES": str(FIXED_NODE_MODULES),
        },
    )

    with pytest.raises(
        RuntimeError,
        match="主表 A:E 有效数据行必须恰好为 51 行.*第 52 行",
    ):
        adapter.extract(input_path, tmp_path / "dataset.json")
