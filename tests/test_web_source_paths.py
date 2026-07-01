from pathlib import Path

import pytest

from xhbx_rag.web.source_paths import (
    SourcePathError,
    can_reveal_source,
    citation_display_excerpt,
    display_location,
    project_root_from_module,
    reveal_in_finder,
    resolve_data_source_path,
    strip_embedded_resource_suffix,
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


def test_display_location_handles_blank_heading_path_items() -> None:
    assert display_location({"heading_path": [" ", ""]}) == "未提供精确位置"


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


def test_resolve_data_source_path_accepts_path_relative_to_data_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "data" / "案例A" / "a.txt"
    source.parent.mkdir(parents=True)
    source.write_text("hello", encoding="utf-8")

    resolved = resolve_data_source_path("案例A/a.txt", project_root=tmp_path)

    assert resolved == source.resolve()
    assert can_reveal_source("案例A/a.txt", project_root=tmp_path) is True


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


def test_resolve_data_source_path_rejects_symlink_outside_data(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "data" / "secret-link.txt"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)

    with pytest.raises(SourcePathError, match="data 目录"):
        resolve_data_source_path("data/secret-link.txt", project_root=tmp_path)


def test_resolve_data_source_path_rejects_directory(tmp_path: Path) -> None:
    source_dir = tmp_path / "data" / "dir"
    source_dir.mkdir(parents=True)

    with pytest.raises(SourcePathError, match="不是普通文件"):
        resolve_data_source_path("data/dir", project_root=tmp_path)


def test_resolve_data_source_path_rejects_absolute_path_outside_data(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(SourcePathError, match="data 目录"):
        resolve_data_source_path(str(outside), project_root=tmp_path)


def test_strip_embedded_resource_suffix_returns_host_path() -> None:
    assert (
        strip_embedded_resource_suffix("data/a.docx::word/media/image1.png")
        == "data/a.docx"
    )


def test_can_reveal_source_returns_true_for_existing_data_file_and_false_for_missing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "data" / "a.txt"
    source.parent.mkdir(parents=True)
    source.write_text("hello", encoding="utf-8")

    assert can_reveal_source("data/a.txt", project_root=tmp_path) is True
    assert can_reveal_source("data/missing.txt", project_root=tmp_path) is False


def test_reveal_in_finder_calls_open_reveal_and_returns_resolved_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "data" / "a.txt"
    source.parent.mkdir(parents=True)
    source.write_text("hello", encoding="utf-8")
    calls: list[tuple[list[str], bool]] = []

    def fake_run(args: list[str], *, check: bool) -> None:
        calls.append((args, check))

    monkeypatch.setattr("xhbx_rag.web.source_paths.subprocess.run", fake_run)

    resolved = reveal_in_finder("data/a.txt", project_root=tmp_path)

    assert calls == [(["open", "-R", str(source.resolve())], True)]
    assert resolved == source.resolve()


def test_project_root_from_module_points_to_repo_root() -> None:
    project_root = project_root_from_module()

    assert (project_root / "pyproject.toml").is_file()
    assert (project_root / "src" / "xhbx_rag").is_dir()
