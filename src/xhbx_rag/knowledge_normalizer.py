from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from .knowledge_domain import (
    CANONICAL_DOMAINS,
    DomainClassification,
    SourceKind,
    apply_domain_metadata,
    infer_chunk_domains,
)
from .models import RagChunk


class UnsupportedKnowledgePath(ValueError):
    pass


@dataclass(frozen=True)
class NormalizationResult:
    success: bool
    report_path: Path
    input_files: int
    chunks: int


def discover_chunk_files(root: Path) -> list[Path]:
    root = Path(root)
    candidates = {
        *root.rglob("chunks.jsonl"),
        *root.rglob("*.chunks.jsonl"),
    }
    return sorted(
        (path for path in candidates if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def source_kind_for_path(root: Path, path: Path) -> SourceKind:
    relative = Path(path).relative_to(root)
    if (
        len(relative.parts) == 2
        and relative.parts[0] == "chunk"
        and relative.name.endswith(".chunks.jsonl")
    ):
        return "培训资料"
    if (
        len(relative.parts) == 2
        and relative.parts[0] != "chunk"
        and relative.name == "chunks.jsonl"
    ):
        return "绩优案例"
    raise UnsupportedKnowledgePath(relative.as_posix())


def normalize_knowledge(input_dir: Path, out_dir: Path) -> NormalizationResult:
    input_dir = Path(input_dir)
    out_dir = Path(out_dir)
    _validate_directories(input_dir, out_dir)
    files = discover_chunk_files(input_dir)
    staging = out_dir.parent / f".{out_dir.name}.tmp-{uuid4().hex}"
    failure_report = out_dir.with_name(f"{out_dir.name}.classification_report.json")
    staging.mkdir(parents=True)

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    file_reports: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    primary_counts: Counter[str] = Counter()
    domain_counts: Counter[str] = Counter()
    samples: dict[str, list[dict[str, Any]]] = {
        domain: [] for domain in CANONICAL_DOMAINS
    }
    seen_ids: dict[str, dict[str, Any]] = {}
    chunk_count = 0
    multi_domain_chunks = 0

    if not files:
        errors.append(_issue("no_chunk_files", "", None, "未发现 chunk JSONL 文件"))

    try:
        for path in files:
            relative = path.relative_to(input_dir).as_posix()
            input_sha256 = _sha256(path)
            file_error_count = len(errors)
            try:
                source_kind = source_kind_for_path(input_dir, path)
            except UnsupportedKnowledgePath:
                errors.append(
                    _issue(
                        "unsupported_path",
                        relative,
                        None,
                        "文件路径不符合培训资料或绩优案例规则",
                    )
                )
                file_reports.append(
                    _file_report(relative, "unsupported_path", 0, input_sha256, None)
                )
                continue

            raw_lines = path.read_text(encoding="utf-8").splitlines()
            nonempty_lines = [
                (line_no, line)
                for line_no, line in enumerate(raw_lines, start=1)
                if line.strip()
            ]
            if not nonempty_lines:
                warnings.append(
                    _issue(
                        "skipped_empty",
                        relative,
                        None,
                        "空文件已跳过",
                    )
                )
                file_reports.append(
                    _file_report(relative, "skipped_empty", 0, input_sha256, None)
                )
                continue

            normalized_chunks: list[RagChunk] = []
            for line_no, line in nonempty_lines:
                chunk = _parse_chunk(line, relative, line_no, errors)
                if chunk is None:
                    continue
                location = {"path": relative, "line": line_no}
                previous = seen_ids.get(chunk.chunk_id)
                if previous is not None:
                    errors.append(
                        {
                            **_issue(
                                "duplicate_chunk_id",
                                relative,
                                line_no,
                                "chunk_id 在输入目录中重复",
                            ),
                            "chunk_id": chunk.chunk_id,
                            "first_location": previous,
                        }
                    )
                else:
                    seen_ids[chunk.chunk_id] = location

                classification = infer_chunk_domains(chunk)
                if classification is None:
                    errors.append(
                        {
                            **_issue(
                                "unclassified",
                                relative,
                                line_no,
                                "未命中足够的一级标签规则",
                            ),
                            "chunk_id": chunk.chunk_id,
                            "inputs": _classification_inputs(chunk),
                        }
                    )
                    continue

                normalized = apply_domain_metadata(
                    chunk,
                    classification,
                    source_kind,
                )
                normalized_chunks.append(normalized)
                chunk_count += 1
                source_counts[source_kind] += 1
                primary_counts[classification.primary_domain] += 1
                for domain in classification.domain_tags:
                    domain_counts[domain] += 1
                if len(classification.domain_tags) > 1:
                    multi_domain_chunks += 1
                _append_sample(
                    samples,
                    classification,
                    chunk.chunk_id,
                    source_kind,
                    relative,
                    line_no,
                )

            output_sha256: str | None = None
            if normalized_chunks:
                output_path = staging / relative
                output_path.parent.mkdir(parents=True, exist_ok=True)
                _write_chunks(output_path, normalized_chunks)
                output_sha256 = _sha256(output_path)
            status = "normalized" if len(errors) == file_error_count else "failed"
            file_reports.append(
                _file_report(
                    relative,
                    status,
                    len(normalized_chunks),
                    input_sha256,
                    output_sha256,
                )
            )

        report = _build_report(
            files=file_reports,
            chunks=chunk_count,
            source_counts=source_counts,
            primary_counts=primary_counts,
            domain_counts=domain_counts,
            multi_domain_chunks=multi_domain_chunks,
            warnings=warnings,
            errors=errors,
            samples=samples,
        )
        if errors:
            shutil.rmtree(staging, ignore_errors=True)
            _write_json_atomic(failure_report, report)
            return NormalizationResult(False, failure_report, len(files), chunk_count)

        report_path = staging / "classification_report.json"
        _write_json_atomic(report_path, report)
        _publish_directory(staging, out_dir)
        failure_report.unlink(missing_ok=True)
        return NormalizationResult(
            True,
            out_dir / "classification_report.json",
            len(files),
            chunk_count,
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _validate_directories(input_dir: Path, out_dir: Path) -> None:
    if not input_dir.is_dir():
        raise ValueError(f"输入目录不存在: {input_dir}")
    input_resolved = input_dir.resolve()
    out_resolved = out_dir.resolve()
    if out_resolved == input_resolved or out_resolved.is_relative_to(input_resolved):
        raise ValueError("输出目录不能位于输入目录内部")
    out_dir.parent.mkdir(parents=True, exist_ok=True)


def _parse_chunk(
    line: str,
    relative: str,
    line_no: int,
    errors: list[dict[str, Any]],
) -> RagChunk | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        errors.append(_issue("invalid_json", relative, line_no, "JSON 解析失败"))
        return None
    try:
        return RagChunk.model_validate(payload)
    except ValidationError as exc:
        fields = sorted(
            {
                ".".join(str(part) for part in item.get("loc", ()))
                for item in exc.errors()
            }
        )
        errors.append(
            {
                **_issue("invalid_chunk", relative, line_no, "RagChunk 字段校验失败"),
                "fields": fields,
            }
        )
        return None


def _classification_inputs(chunk: RagChunk) -> dict[str, Any]:
    metadata = chunk.metadata
    return {
        key: metadata.get(key)
        for key in ("title", "category", "scenario", "tags")
        if metadata.get(key)
    }


def _append_sample(
    samples: dict[str, list[dict[str, Any]]],
    classification: DomainClassification,
    chunk_id: str,
    source_kind: SourceKind,
    path: str,
    line_no: int,
) -> None:
    for domain in classification.domain_tags:
        if len(samples[domain]) >= 3:
            continue
        hits = [
            {
                "field": hit.field,
                "rule": hit.rule,
                "points": hit.points,
            }
            for hit in classification.hits
            if hit.domain == domain
        ]
        samples[domain].append(
            {
                "chunk_id": chunk_id,
                "source_kind": source_kind,
                "path": path,
                "line": line_no,
                "score": classification.scores[domain],
                "hits": hits,
            }
        )


def _build_report(
    *,
    files: list[dict[str, Any]],
    chunks: int,
    source_counts: Counter[str],
    primary_counts: Counter[str],
    domain_counts: Counter[str],
    multi_domain_chunks: int,
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    samples: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    return {
        "success": not errors,
        "counts": {
            "input_files": len(files),
            "valid_files": sum(item["status"] == "normalized" for item in files),
            "empty_files": sum(item["status"] == "skipped_empty" for item in files),
            "chunks": chunks,
            "multi_domain_chunks": multi_domain_chunks,
        },
        "source_kind_distribution": dict(sorted(source_counts.items())),
        "primary_domain_distribution": _ordered_domain_counts(primary_counts),
        "domain_tag_distribution": _ordered_domain_counts(domain_counts),
        "warnings": warnings,
        "errors": errors,
        "samples": {domain: samples[domain] for domain in CANONICAL_DOMAINS},
        "files": files,
    }


def _ordered_domain_counts(counts: Counter[str]) -> dict[str, int]:
    return {domain: counts[domain] for domain in CANONICAL_DOMAINS if counts[domain]}


def _issue(
    code: str,
    path: str,
    line: int | None,
    message: str,
) -> dict[str, Any]:
    issue: dict[str, Any] = {"code": code, "path": path, "message": message}
    if line is not None:
        issue["line"] = line
    return issue


def _file_report(
    path: str,
    status: str,
    chunks: int,
    input_sha256: str,
    output_sha256: str | None,
) -> dict[str, Any]:
    return {
        "path": path,
        "status": status,
        "chunks": chunks,
        "input_sha256": input_sha256,
        "output_sha256": output_sha256,
    }


def _write_chunks(path: Path, chunks: list[RagChunk]) -> None:
    lines = [
        json.dumps(
            chunk.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for chunk in chunks
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_directory(staging: Path, out_dir: Path) -> None:
    backup = out_dir.with_name(f".{out_dir.name}.backup-{uuid4().hex}")
    moved_existing = False
    try:
        if out_dir.exists():
            os.replace(out_dir, backup)
            moved_existing = True
        os.replace(staging, out_dir)
    except Exception:
        if moved_existing and backup.exists() and not out_dir.exists():
            os.replace(backup, out_dir)
        raise
    else:
        if moved_existing:
            shutil.rmtree(backup)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
