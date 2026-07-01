from pathlib import Path

import pytest

from xhbx_rag.web.source_paths import (
    SourcePathError,
    citation_display_excerpt,
    display_location,
    resolve_data_source_path,
)


def test_display_location_formats_line_span() -> None:
    assert display_location({"line_start": 2, "line_end": 4}) == "L2-L4"


def test_display_location_formats_page_slide_and_heading() -> None:
    assert (
        display_location(
            {
                "page": 3,
                "slide": 5,
                "line_start": 9,
                "line_end": 9,
                "heading_path": ["第1章", "保险整理"],
            }
        )
        == "p3 · slide5 · L9 · 第1章 / 保险整理"
    )


def test_display_location_handles_missing_locator() -> None:
    assert display_location({}) == "未提供精确位置"


def test_citation_display_excerpt_prefers_source_excerpt() -> None:
    citation = {"source_excerpt": "原文摘录", "quote": "模型引用"}

    assert citation_display_excerpt(citation) == "原文摘录"


def test_citation_display_excerpt_falls_back_to_quote() -> None:
    citation = {"source_excerpt": "", "quote": "模型引用"}

    assert citation_display_excerpt(citation) == "模型引用"


def test_resolve_data_source_path_accepts_data_file(tmp_path: Path) -> None:
    project_root = tmp_path
    source = project_root / "data" / "案例A" / "第1节.track-0.txt"
    source.parent.mkdir(parents=True)
    source.write_text("hello", encoding="utf-8")

    resolved = resolve_data_source_path(
        "data/案例A/第1节.track-0.txt",
        project_root=project_root,
    )

    assert resolved == source.resolve()


def test_resolve_data_source_path_maps_embedded_resource_to_host_file(
    tmp_path: Path,
) -> None:
    project_root = tmp_path
    source = project_root / "data" / "案例A" / "讲义.docx"
    source.parent.mkdir(parents=True)
    source.write_text("docx bytes", encoding="utf-8")

    resolved = resolve_data_source_path(
        "data/案例A/讲义.docx::word/media/image1.png",
        project_root=project_root,
    )

    assert resolved == source.resolve()


def test_resolve_data_source_path_rejects_parent_traversal(tmp_path: Path) -> None:
    with pytest.raises(SourcePathError, match="data 目录"):
        resolve_data_source_path("../secret.txt", project_root=tmp_path)


def test_resolve_data_source_path_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SourcePathError, match="文件不存在"):
        resolve_data_source_path("data/missing.txt", project_root=tmp_path)
