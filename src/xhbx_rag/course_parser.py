"""课程目录的递归扫描、切块与产物输出。

一个课件/教材文件是一个知识单元（与案例管线"目录=章节"不同）。
单个文件解析失败不拖垮整批；enrich 失败降级为纯规则产物。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .chunk_io import chunk_text_hash
from .course_chunk_builder import build_course_chunks
from .course_enrichment import CourseEnrichment, CourseEnrichmentAgent
from .models import RagChunk
from .observability import TraceSink, emit_trace
from .sales_generation import _atomic_write_text
from .source_loader import SUPPORTED_EXTENSIONS, parse_source_file

_ENRICH_SAMPLE_MAX_CHARS = 4000


class CourseFileParseError(RuntimeError):
    """严格课程模式下，单个受支持文件的必需加工失败。"""

    def __init__(self, relative_path: str, detail: str) -> None:
        super().__init__(f"{relative_path}: {detail}")
        self.relative_path = relative_path
        self.detail = detail


@dataclass(frozen=True)
class CourseParseReport:
    input_dir: str
    output_files: dict[str, str]
    counts: dict[str, int]
    skipped_files: list[str] = field(default_factory=list)
    failed_files: list[dict[str, str]] = field(default_factory=list)
    enrich_failures: list[str] = field(default_factory=list)
    duplicate_text_hashes: dict[str, list[str]] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "input_dir": self.input_dir,
                "output_files": self.output_files,
                "counts": self.counts,
                "skipped_files": self.skipped_files,
                "failed_files": self.failed_files,
                "enrich_failures": self.enrich_failures,
                "duplicate_text_hashes": self.duplicate_text_hashes,
            },
            ensure_ascii=False,
            indent=2,
        )


def parse_course_dir(
    course_dir: Path,
    out_dir: Path,
    enrichment_agent: CourseEnrichmentAgent | None = None,
    trace: TraceSink | None = None,
    *,
    fail_fast: bool = False,
    on_file: Callable[[str, str, int], None] | None = None,
) -> CourseParseReport:
    if not course_dir.is_dir():
        raise NotADirectoryError(f"课程目录不存在或不是目录: {course_dir}")

    chunks_path = out_dir / "chunks.jsonl"
    report_path = out_dir / "parse_report.json"
    if fail_fast:
        chunks_path.unlink(missing_ok=True)
        report_path.unlink(missing_ok=True)

    supported, skipped = _scan_files(course_dir)
    emit_trace(
        trace,
        "course_parse.scan_completed",
        {
            "course_dir": str(course_dir),
            "supported_count": len(supported),
            "skipped_count": len(skipped),
        },
    )

    all_chunks: list[RagChunk] = []
    failed_files: list[dict[str, str]] = []
    enrich_failures: list[str] = []
    source_hashes: dict[str, list[str]] = {}
    for path in supported:
        relative = str(path.relative_to(course_dir))
        try:
            source = parse_source_file(path, base_dir=course_dir)
            if source.is_empty:
                # source_loader 把解析失败/无文本归一为空文本 + warnings，
                # 课程管线将其视为文件级失败（如损坏文件、扫描件 pdf）
                raise ValueError("；".join(source.warnings) or "文件无可提取文本")
            enrichment = _enrich_or_none(
                enrichment_agent, source, enrich_failures, trace
            )
            chunks = build_course_chunks(source, enrichment=enrichment)
            source_hash = chunk_text_hash(source.text)
        except Exception as exc:  # noqa: BLE001 - 单文件失败不拖垮整批
            failed_files.append({"path": relative, "error": repr(exc)})
            emit_trace(
                trace,
                "course_parse.file_failed",
                {"path": relative, "error": repr(exc)},
            )
            if on_file is not None:
                on_file(relative, "failed", 0)
            if fail_fast:
                raise CourseFileParseError(
                    relative,
                    "课程文件解析或切分失败",
                ) from exc
            continue
        source_hashes.setdefault(source_hash, []).append(relative)
        all_chunks.extend(chunks)
        emit_trace(
            trace,
            "course_parse.file_parsed",
            {"path": relative, "chunk_count": len(chunks)},
        )
        if on_file is not None:
            on_file(relative, "parsed", len(chunks))

    duplicates = {key: paths for key, paths in source_hashes.items() if len(paths) > 1}

    out_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        chunks_path,
        "\n".join(chunk.model_dump_json() for chunk in all_chunks),
    )

    report = CourseParseReport(
        input_dir=str(course_dir),
        output_files={
            "chunks": str(chunks_path),
            "report": str(report_path),
        },
        counts={
            "files_parsed": len(supported) - len(failed_files),
            "files_skipped": len(skipped),
            "files_failed": len(failed_files),
            "chunks": len(all_chunks),
            "duplicate_text_hashes": len(duplicates),
        },
        skipped_files=skipped,
        failed_files=failed_files,
        enrich_failures=enrich_failures,
        duplicate_text_hashes=duplicates,
    )
    _atomic_write_text(report_path, report.to_json())
    emit_trace(trace, "course_parse.completed", {"counts": report.counts})
    return report


def _scan_files(course_dir: Path) -> tuple[list[Path], list[str]]:
    supported: list[Path] = []
    skipped: list[str] = []
    for path in sorted(course_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith(("~$", ".")):
            skipped.append(str(path.relative_to(course_dir)))
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            skipped.append(str(path.relative_to(course_dir)))
            continue
        supported.append(path)
    return supported, skipped


def _enrich_or_none(
    agent: CourseEnrichmentAgent | None,
    source: object,
    enrich_failures: list[str],
    trace: TraceSink | None,
) -> CourseEnrichment | None:
    if agent is None:
        return None
    course_path = Path(str(getattr(source, "source_path", "")))
    course_name = course_path.stem
    course_series = "" if str(course_path.parent) == "." else str(course_path.parent)
    try:
        return agent.enrich(
            course_name=course_name,
            course_series=course_series,
            sample_text=str(getattr(source, "text", ""))[:_ENRICH_SAMPLE_MAX_CHARS],
        )
    except Exception as exc:  # noqa: BLE001 - enrich 失败降级为纯规则产物
        enrich_failures.append(f"{course_name}: {exc!r}")
        emit_trace(
            trace,
            "course_parse.enrich_failed",
            {"course_name": course_name, "error": repr(exc)},
        )
        return None
