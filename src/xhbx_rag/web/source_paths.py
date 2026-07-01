from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Mapping


class SourcePathError(ValueError):
    """Raised when a citation source path cannot be safely resolved."""


def project_root_from_module() -> Path:
    return Path(__file__).resolve().parents[3]


def display_location(locator: Mapping[str, Any] | None) -> str:
    if not isinstance(locator, Mapping) or not locator:
        return "未提供精确位置"

    parts: list[str] = []
    if locator.get("page"):
        parts.append(f"p{locator['page']}")
    if locator.get("slide"):
        parts.append(f"slide{locator['slide']}")

    line_start = locator.get("line_start")
    line_end = locator.get("line_end")
    if line_start and line_end and line_start != line_end:
        parts.append(f"L{line_start}-L{line_end}")
    elif line_start:
        parts.append(f"L{line_start}")

    heading_path = locator.get("heading_path")
    if isinstance(heading_path, list) and heading_path:
        parts.append(" / ".join(str(item) for item in heading_path if str(item).strip()))
    elif isinstance(heading_path, str) and heading_path.strip():
        parts.append(heading_path.strip())

    return " · ".join(parts) if parts else "未提供精确位置"


def citation_display_excerpt(citation: Mapping[str, Any]) -> str:
    source_excerpt = str(citation.get("source_excerpt") or "").strip()
    if source_excerpt:
        return source_excerpt
    return str(citation.get("quote") or "").strip()


def strip_embedded_resource_suffix(source_path: str) -> str:
    return source_path.split("::", 1)[0]


def resolve_data_source_path(
    source_path: str,
    *,
    project_root: Path | None = None,
) -> Path:
    root = (project_root or project_root_from_module()).resolve()
    data_root = (root / "data").resolve()
    raw_path = strip_embedded_resource_suffix(source_path).strip()
    if not raw_path:
        raise SourcePathError("引用路径为空")

    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()

    try:
        resolved.relative_to(data_root)
    except ValueError as exc:
        raise SourcePathError("引用路径必须位于 data 目录内") from exc

    if not resolved.exists():
        raise SourcePathError(f"文件不存在: {resolved}")
    if not resolved.is_file():
        raise SourcePathError(f"引用路径不是普通文件: {resolved}")

    return resolved


def can_reveal_source(source_path: str, *, project_root: Path | None = None) -> bool:
    try:
        resolve_data_source_path(source_path, project_root=project_root)
    except SourcePathError:
        return False
    return True


def reveal_in_finder(source_path: str, *, project_root: Path | None = None) -> Path:
    resolved = resolve_data_source_path(source_path, project_root=project_root)
    subprocess.run(["open", "-R", str(resolved)], check=True)
    return resolved
