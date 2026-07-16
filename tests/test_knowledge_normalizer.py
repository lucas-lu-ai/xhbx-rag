from __future__ import annotations

import json
from pathlib import Path

import pytest

from xhbx_rag.chunk_io import load_chunks_jsonl
from xhbx_rag.knowledge_normalizer import (
    UnsupportedKnowledgePath,
    discover_chunk_files,
    normalize_knowledge,
    source_kind_for_path,
)
from xhbx_rag.models import RagChunk


def _chunk(
    chunk_id: str,
    *,
    metadata: dict | None = None,
    text: str = "知识正文",
) -> RagChunk:
    return RagChunk(
        chunk_id=chunk_id,
        chunk_type="knowledge_entry",
        text=text,
        metadata=metadata if metadata is not None else {"tags": ["产品知识"]},
        citations=[],
        source_file="培训材料.pptx",
    )


def _write_jsonl(path: Path, chunks: list[RagChunk]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(
        json.dumps(chunk.model_dump(mode="json"), ensure_ascii=False)
        for chunk in chunks
    )
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_discovery_dedupes_both_patterns_and_sorts_relative_paths(
    tmp_path: Path,
) -> None:
    _write_jsonl(tmp_path / "chunk" / "b.chunks.jsonl", [_chunk("b")])
    _write_jsonl(tmp_path / "案例A" / "chunks.jsonl", [_chunk("a")])
    (tmp_path / "ignore.jsonl").write_text("{}\n", encoding="utf-8")

    relative_paths = [
        path.relative_to(tmp_path).as_posix()
        for path in discover_chunk_files(tmp_path)
    ]

    assert relative_paths == ["chunk/b.chunks.jsonl", "案例A/chunks.jsonl"]


def test_source_kind_is_derived_only_from_supported_relative_paths(
    tmp_path: Path,
) -> None:
    assert (
        source_kind_for_path(
            tmp_path,
            tmp_path / "chunk" / "培训课件.chunks.jsonl",
        )
        == "培训资料"
    )
    assert (
        source_kind_for_path(
            tmp_path,
            tmp_path / "绩优案例" / "chunks.jsonl",
        )
        == "绩优案例"
    )
    with pytest.raises(UnsupportedKnowledgePath):
        source_kind_for_path(
            tmp_path,
            tmp_path / "其他目录" / "错误命名.chunks.jsonl",
        )


def test_normalize_preserves_relative_path_and_original_chunk_fields(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "parsed"
    out_dir = tmp_path / "normalized"
    original = _chunk(
        "id-1",
        metadata={"title": "保险产品保障责任", "legacy": "保留"},
        text="不能修改的正文",
    )
    _write_jsonl(input_dir / "chunk" / "产品.chunks.jsonl", [original])

    result = normalize_knowledge(input_dir, out_dir)

    normalized_path = out_dir / "chunk" / "产品.chunks.jsonl"
    normalized = load_chunks_jsonl(normalized_path)[0]
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert result.success is True
    assert normalized.chunk_id == original.chunk_id
    assert normalized.text == original.text
    assert normalized.citations == original.citations
    assert normalized.source_file == original.source_file
    assert normalized.metadata["legacy"] == "保留"
    assert normalized.metadata["source_kind"] == "培训资料"
    assert normalized.metadata["primary_domain"] == "产品知识"
    assert report["counts"]["input_files"] == 1
    assert report["counts"]["chunks"] == 1
    assert report["files"][0]["input_sha256"]
    assert report["files"][0]["output_sha256"]


def test_empty_file_and_duplicate_ids_warn_but_bad_json_fails_without_publish(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "parsed"
    out_dir = tmp_path / "normalized"
    _write_jsonl(
        input_dir / "chunk" / "ok.chunks.jsonl",
        [_chunk("same", metadata={"tags": ["销售技能"]})],
    )
    empty_path = input_dir / "chunk" / "empty.chunks.jsonl"
    empty_path.parent.mkdir(parents=True, exist_ok=True)
    empty_path.write_text("", encoding="utf-8")
    _write_jsonl(
        input_dir / "chunk" / "duplicate.chunks.jsonl",
        [_chunk("same", metadata={"tags": ["客户经营"]})],
    )
    bad_path = input_dir / "案例B" / "chunks.jsonl"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text("{bad json}\n", encoding="utf-8")

    result = normalize_knowledge(input_dir, out_dir)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert result.success is False
    assert not out_dir.exists()
    assert report["counts"]["empty_files"] == 1
    assert {error["code"] for error in report["errors"]} == {"invalid_json"}
    assert {warning["code"] for warning in report["warnings"]} == {
        "deduplicated_chunk_id",
        "skipped_empty",
    }
    assert report["counts"]["deduplicated_chunks"] == 1


def test_duplicate_id_across_source_kinds_is_a_blocking_conflict(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "parsed"
    out_dir = tmp_path / "normalized"
    _write_jsonl(input_dir / "chunk" / "course.chunks.jsonl", [_chunk("same")])
    _write_jsonl(input_dir / "案例A" / "chunks.jsonl", [_chunk("same")])

    result = normalize_knowledge(input_dir, out_dir)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert result.success is False
    assert not out_dir.exists()
    assert {error["code"] for error in report["errors"]} == {
        "duplicate_chunk_id_source_conflict"
    }


def test_duplicate_ids_keep_first_stable_record_and_report_both_locations(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "parsed"
    out_dir = tmp_path / "normalized"
    _write_jsonl(
        input_dir / "chunk" / "a.chunks.jsonl",
        [_chunk("same", text="首个稳定版本")],
    )
    _write_jsonl(
        input_dir / "chunk" / "b.chunks.jsonl",
        [
            _chunk("same", text="后续抽取版本"),
            _chunk("unique", text="唯一记录"),
        ],
    )

    result = normalize_knowledge(input_dir, out_dir)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    first = load_chunks_jsonl(out_dir / "chunk" / "a.chunks.jsonl")
    second = load_chunks_jsonl(out_dir / "chunk" / "b.chunks.jsonl")
    warning = next(
        item
        for item in report["warnings"]
        if item["code"] == "deduplicated_chunk_id"
    )
    assert result.success is True
    assert report["counts"]["input_chunks"] == 3
    assert report["counts"]["chunks"] == 2
    assert report["counts"]["deduplicated_chunks"] == 1
    assert first[0].text == "首个稳定版本"
    assert [chunk.chunk_id for chunk in second] == ["unique"]
    assert warning["first_location"] == {
        "path": "chunk/a.chunks.jsonl",
        "line": 1,
    }


def test_unclassified_chunk_fails_without_silent_fallback(tmp_path: Path) -> None:
    input_dir = tmp_path / "parsed"
    out_dir = tmp_path / "normalized"
    _write_jsonl(
        input_dir / "chunk" / "unknown.chunks.jsonl",
        [_chunk("unknown", metadata={}, text="没有业务关键词的普通文字")],
    )

    result = normalize_knowledge(input_dir, out_dir)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert result.success is False
    assert not out_dir.exists()
    assert report["errors"][0]["code"] == "unclassified"
    assert "行业与公司" not in report["errors"][0].values()


def test_failed_rerun_preserves_previously_published_output(tmp_path: Path) -> None:
    input_dir = tmp_path / "parsed"
    out_dir = tmp_path / "normalized"
    _write_jsonl(input_dir / "chunk" / "ok.chunks.jsonl", [_chunk("ok")])
    first = normalize_knowledge(input_dir, out_dir)
    assert first.success is True
    published = _snapshot(out_dir)
    bad_path = input_dir / "案例A" / "chunks.jsonl"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text("not-json\n", encoding="utf-8")

    second = normalize_knowledge(input_dir, out_dir)

    assert second.success is False
    assert _snapshot(out_dir) == published
    assert second.report_path.parent == tmp_path


def test_successful_normalization_is_deterministic_across_output_directories(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "parsed"
    first_out = tmp_path / "normalized-a"
    second_out = tmp_path / "normalized-b"
    _write_jsonl(
        input_dir / "chunk" / "产品.chunks.jsonl",
        [_chunk("id-1", metadata={"tags": ["产品知识", "合规与风控"]})],
    )

    first = normalize_knowledge(input_dir, first_out)
    second = normalize_knowledge(input_dir, second_out)

    assert first.success and second.success
    assert _snapshot(first_out) == _snapshot(second_out)
    assert not list(tmp_path.glob(".normalized-*.tmp-*"))


def test_output_directory_cannot_be_inside_input_directory(tmp_path: Path) -> None:
    input_dir = tmp_path / "parsed"
    input_dir.mkdir()

    with pytest.raises(ValueError, match="输出目录不能位于输入目录内部"):
        normalize_knowledge(input_dir, input_dir / "normalized")
