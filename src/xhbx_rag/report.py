from __future__ import annotations

from pathlib import Path

from .models import ParseReport, RagChunk, StructuredCaseKnowledge


def build_parse_report(
    insights_path: Path,
    playbook_path: Path | None,
    out_dir: Path,
    knowledge: StructuredCaseKnowledge,
    chunks: list[RagChunk],
    warnings: list[str],
    errors: list[str] | None = None,
) -> ParseReport:
    case_dir = out_dir / knowledge.case_id
    return ParseReport(
        input_files={
            "insights": str(insights_path),
            "playbook": str(playbook_path) if playbook_path is not None else None,
        },
        output_files={
            "structured": str(case_dir / "case.structured.json"),
            "chunks": str(case_dir / "chunks.jsonl"),
            "report": str(case_dir / "parse_report.json"),
        },
        case_name=knowledge.case_name,
        counts={
            "customer_journey": len(knowledge.customer_journey),
            "strategies": len(knowledge.strategies),
            "scripts": len(knowledge.scripts),
            "objection_handling": len(knowledge.objection_handling),
            "chunks": len(chunks),
        },
        warnings=warnings,
        errors=errors or [],
    )
