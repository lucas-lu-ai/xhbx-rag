from __future__ import annotations

import hashlib
import re
import unicodedata

from .models import StructuredCaseKnowledge
from .parser import ParsedInputs


_UNSAFE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def make_case_id(case_name: str) -> str:
    normalized = unicodedata.normalize("NFKC", case_name).strip().lower()
    base = _UNSAFE.sub("_", normalized).strip("_")
    digest = hashlib.sha1(case_name.encode("utf-8")).hexdigest()[:10]
    if not base:
        base = "case"
    if len(base) > 80:
        base = base[:80].rstrip("_")
    return f"{base}_{digest}"


def normalize_case(parsed: ParsedInputs) -> StructuredCaseKnowledge:
    source_files = [parsed.insights_path.name]
    if parsed.playbook_path is not None:
        source_files.append(parsed.playbook_path.name)

    return StructuredCaseKnowledge(
        case_id=make_case_id(parsed.source.case_name),
        case_name=parsed.source.case_name,
        case_summary=parsed.source.case_summary,
        source_files=source_files,
        customer_journey=parsed.source.customer_journey,
        strategies=parsed.source.strategies,
        scripts=parsed.source.scripts,
        objection_handling=parsed.source.objection_handling,
    )
