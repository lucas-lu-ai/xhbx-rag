from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from .models import ParseReport, RagChunk, StructuredCaseKnowledge


@dataclass(frozen=True)
class OutputPaths:
    structured_path: Path
    chunks_path: Path
    report_path: Path


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _dump_model(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2)


def write_outputs(
    out_dir: Path,
    knowledge: StructuredCaseKnowledge,
    chunks: list[RagChunk],
    report: ParseReport,
) -> OutputPaths:
    case_dir = out_dir / knowledge.case_id
    structured_path = case_dir / "case.structured.json"
    chunks_path = case_dir / "chunks.jsonl"
    report_path = case_dir / "parse_report.json"

    report = report.model_copy(
        update={
            "output_files": {
                "structured": str(structured_path),
                "chunks": str(chunks_path),
                "report": str(report_path),
            }
        }
    )

    _atomic_write(structured_path, _dump_model(knowledge) + "\n")
    chunk_lines = [
        json.dumps(chunk.model_dump(mode="json"), ensure_ascii=False)
        for chunk in chunks
    ]
    _atomic_write(chunks_path, "\n".join(chunk_lines) + ("\n" if chunk_lines else ""))
    _atomic_write(report_path, _dump_model(report) + "\n")
    return OutputPaths(
        structured_path=structured_path,
        chunks_path=chunks_path,
        report_path=report_path,
    )
