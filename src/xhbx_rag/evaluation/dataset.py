from __future__ import annotations

import json
from pathlib import Path

from xhbx_rag.evaluation.models import EvaluationItem


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PARSED_ROOT = REPO_ROOT / "docs"


def load_dataset(path: Path) -> list[EvaluationItem]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("评测项") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != 50:
        raise ValueError("评测集必须包含 50 条评测项")

    excel_rows: list[int] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("评测项必须是包含中文字段的对象")
        excel_row = row.get("Excel行号")
        if not isinstance(excel_row, int) or isinstance(excel_row, bool):
            raise ValueError("Excel行号缺失或不是整数")
        excel_rows.append(excel_row)

    expected_rows = set(range(2, 52))
    actual_rows = set(excel_rows)
    errors: list[str] = []
    duplicate_rows = sorted(
        value for value in actual_rows if excel_rows.count(value) > 1
    )
    if duplicate_rows:
        errors.append(
            "Excel行号重复：" + "、".join(str(value) for value in duplicate_rows)
        )
    if actual_rows != expected_rows:
        missing = "、".join(str(value) for value in sorted(expected_rows - actual_rows))
        outside = "、".join(str(value) for value in sorted(actual_rows - expected_rows))
        errors.append(
            "Excel行号必须恰好为 2..51；"
            f"缺失：{missing or '无'}；越界：{outside or '无'}"
        )
    if errors:
        raise ValueError("；".join(errors))

    for row, excel_row in zip(rows, excel_rows, strict=True):
        expected_id = f"row-{excel_row}"
        actual_id = row.get("评测项ID")
        if actual_id != expected_id:
            raise ValueError(
                "评测项ID与Excel行号不一致："
                f"Excel行号 {excel_row} 应为 {expected_id}，"
                f"实际为 {actual_id if actual_id is not None else '缺失'}"
            )

    items = [EvaluationItem.model_validate(row) for row in rows]
    return sorted(items, key=lambda item: item.excel_row)


def load_chunk_catalog(parsed_root: Path | None = None) -> set[str]:
    root = parsed_root if parsed_root is not None else DEFAULT_PARSED_ROOT
    chunk_ids: set[str] = set()
    for path in sorted(root.glob("**/chunks.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            chunk_id = str(json.loads(line).get("chunk_id", "")).strip()
            if chunk_id:
                chunk_ids.add(chunk_id)
    return chunk_ids
