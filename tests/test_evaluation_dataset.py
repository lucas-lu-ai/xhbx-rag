from __future__ import annotations

import json
from collections import Counter
from hashlib import sha256
from pathlib import Path

import pytest

import xhbx_rag.evaluation.dataset as dataset_module
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

    adapter.extract(tmp_path / "input.xlsx", tmp_path / "dataset.json")
    adapter.backfill(
        tmp_path / "input.xlsx",
        tmp_path / "payload.json",
        tmp_path / "output.xlsx",
    )
    adapter.verify(
        tmp_path / "input.xlsx",
        tmp_path / "snapshot.json",
        tmp_path / "output.xlsx",
        tmp_path / "previews",
    )

    adapter_dir = tmp_path / "run" / ".workbook_adapter"
    copied_script = adapter_dir / "evaluation_workbook.mjs"
    linked_modules = adapter_dir / "node_modules"
    assert copied_script.read_bytes() == (
        REPO_ROOT / "scripts" / "evaluation_workbook.mjs"
    ).read_bytes()
    assert linked_modules.is_symlink()
    assert linked_modules.resolve() == node_modules.resolve()
    calls = calls_path.read_text(encoding="utf-8")
    assert "\nextract\n" in calls
    assert "\nbackfill\n" in calls
    assert "\nverify\n" in calls
    assert str(fake_node) not in calls


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
    assert _sha256(REAL_WORKBOOK) == before_sha
