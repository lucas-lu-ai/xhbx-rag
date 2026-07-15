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

    items = [EvaluationItem.model_validate(row) for row in rows]
    excel_rows = [item.excel_row for item in items]
    if len(excel_rows) != len(set(excel_rows)):
        raise ValueError("Excel行号重复")
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
