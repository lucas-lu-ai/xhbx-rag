from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .source_paths import project_root_from_module


BAD_CASES_RELATIVE_PATH = Path(".local") / "bad_cases" / "bad_cases.jsonl"


def save_bad_case(
    payload: Mapping[str, Any],
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    root = project_root or project_root_from_module()
    path = root / BAD_CASES_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "bad_case_id": f"bad-{uuid4().hex}",
        "created_at": datetime.now(UTC).isoformat(),
        **dict(payload),
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    return {
        "ok": True,
        "bad_case_id": record["bad_case_id"],
        "path": str(path),
    }
