"""培训课程素材的规则切块。

与案例管线的 `chunk_builder`（消费 LLM 抽取结果）不同，课程管线直接
对 `source_loader` 解析出的课件/教材文本做规则切块：
- pptx/pdf 按 `## 第 N 页` 页块切分，短页合并、长页按段落拆分；
- pptx 页块内 `### 讲师备注` 做结构解析（教学目标进 metadata，
  教学时间/教学方式剔除）；
- docx 按标题层级切分，无标题时按段落窗口切；
- 每门课额外产出一个概览 chunk，可选拼入课程级 LLM 增值产物。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from .course_enrichment import CourseEnrichment
from .models import EvidenceRef, RagChunk
from .normalizer import make_case_id
from .source_loader import ParsedSourceFile
from .tagging import tag_chunk

MIN_CHUNK_CHARS = 100
MAX_CHUNK_CHARS = 2000
_OVERVIEW_BODY_MAX_CHARS = 800
_QUOTE_MAX_CHARS = 120

_PAGE_HEADER_RE = re.compile(r"^## 第 (\d+) 页$", re.MULTILINE)
_NOTES_MARKER = "### 讲师备注"
# 字段行可能带项目符号前缀（如 "•      教学时间：2分钟"）
_NOTES_FIELD_RE = re.compile(
    r"^[•·◆▪○\-\*\s]*(教学时间|教学目标|教学方式|教学流程及要点|辅助资料)[：:]\s*",
    re.MULTILINE,
)
_DROPPED_NOTE_FIELDS = {"教学时间", "教学方式"}
_HEADING_RE = re.compile(r"^(#{1,2})\s+(.+?)\s*$")

_AUDIENCE_RULES: tuple[tuple[str, str], ...] = (
    ("新人", "新人"),
    ("主管", "主管"),
    ("绩优", "绩优"),
    ("top5000", "绩优"),
    ("ida", "绩优"),
    ("讲师", "讲师"),
    ("师资", "讲师"),
)
_DEFAULT_AUDIENCE = "通用"


@dataclass(frozen=True)
class _Segment:
    """切块中间产物：一段文本与它的定位范围。"""

    text: str
    unit_start: int
    unit_end: int
    heading: str = ""


def build_course_chunks(
    source: ParsedSourceFile,
    enrichment: CourseEnrichment | None = None,
) -> list[RagChunk]:
    course_path = PurePosixPath(source.source_path)
    course_name = course_path.stem
    course_series = "" if course_path.parent == PurePosixPath(".") else str(course_path.parent)
    course_id = make_case_id(str(course_path.with_suffix("")))
    audience = _infer_audience(source.source_path, enrichment)

    if source.source_type in ("pptx", "pdf"):
        segments = _segments_from_pages(source.text, keep_notes=source.source_type == "pptx")
        unit_label = "slide" if source.source_type == "pptx" else "page"
    else:
        segments = _segments_from_headings(source.text)
        unit_label = ""

    base_metadata = {
        "course_name": course_name,
        "course_series": course_series,
        "audience": audience,
    }

    chunks = [
        _overview_chunk(
            source=source,
            course_id=course_id,
            base_metadata=base_metadata,
            segments=segments,
            enrichment=enrichment,
        )
    ]
    for index, segment in enumerate(segments, start=1):
        chunks.append(
            _segment_chunk(
                source=source,
                course_id=course_id,
                base_metadata=base_metadata,
                segment=segment,
                index=index,
                unit_label=unit_label,
            )
        )
    return [tag_chunk(chunk) for chunk in chunks]


def _infer_audience(source_path: str, enrichment: CourseEnrichment | None) -> str:
    if enrichment is not None and enrichment.audience.strip():
        return enrichment.audience.strip()
    haystack = source_path.lower()
    for keyword, audience in _AUDIENCE_RULES:
        if keyword in haystack:
            return audience
    return _DEFAULT_AUDIENCE


# ---------------------------------------------------------------------------
# 页式素材（pptx / pdf）


def _segments_from_pages(text: str, keep_notes: bool) -> list[_Segment]:
    pages = _split_pages(text)
    merged = _merge_short_pages(pages)
    segments: list[_Segment] = []
    for number_start, number_end, body in merged:
        for part in _split_long_text(body):
            segments.append(
                _Segment(text=part, unit_start=number_start, unit_end=number_end)
            )
    return segments


def _split_pages(text: str) -> list[tuple[int, str]]:
    matches = list(_PAGE_HEADER_RE.finditer(text))
    if not matches:
        stripped = text.strip()
        if not stripped:
            return []
        return [(1, _parse_notes(stripped))]
    pages: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = _parse_notes(text[start:end].strip())
        if body:
            pages.append((int(match.group(1)), body))
    return pages


def _parse_notes(page_body: str) -> str:
    """过滤讲师备注中无检索价值的字段（教学时间/教学方式）。

    教学目标保留在文本中，后续由 `_extract_goals_for_metadata`
    统一从 chunk 文本抽取进 metadata，保持单一来源。
    """
    if _NOTES_MARKER not in page_body:
        return page_body
    main, _, notes = page_body.partition(_NOTES_MARKER)
    kept_fields = _filter_note_fields(notes.strip())
    parts = [main.strip()]
    if kept_fields:
        parts.append(f"{_NOTES_MARKER}\n{kept_fields}")
    return "\n".join(part for part in parts if part)


def _filter_note_fields(notes: str) -> str:
    matches = list(_NOTES_FIELD_RE.finditer(notes))
    if not matches:
        return notes
    kept: list[str] = []
    prefix = notes[: matches[0].start()].strip()
    if prefix:
        kept.append(prefix)
    for index, match in enumerate(matches):
        field = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(notes)
        value = notes[start:end].strip()
        if field in _DROPPED_NOTE_FIELDS:
            continue
        if value:
            kept.append(f"{field}：{value}")
    return "\n".join(kept)


def _split_goal_values(value: str) -> list[str]:
    goals = [item.strip().rstrip("；;。") for item in re.split(r"[;；\n]", value)]
    return [goal for goal in goals if goal]


def _merge_short_pages(pages: list[tuple[int, str]]) -> list[tuple[int, int, str]]:
    merged: list[tuple[int, int, str]] = []
    buffer_start: int | None = None
    buffer_end = 0
    buffer_parts: list[str] = []

    def flush() -> None:
        nonlocal buffer_start, buffer_parts
        if buffer_start is None:
            return
        merged.append((buffer_start, buffer_end, "\n\n".join(buffer_parts)))
        buffer_start = None
        buffer_parts = []

    for number, body in pages:
        if buffer_start is None:
            buffer_start = number
        buffer_end = number
        buffer_parts.append(body)
        if len("\n\n".join(buffer_parts)) >= MIN_CHUNK_CHARS:
            flush()
    if buffer_start is not None:
        # 结尾残留的短页并入前一个块，避免产出信息量过低的碎块
        if merged and len("\n\n".join(buffer_parts)) < MIN_CHUNK_CHARS:
            last_start, _, last_body = merged[-1]
            merged[-1] = (
                last_start,
                buffer_end,
                f"{last_body}\n\n" + "\n\n".join(buffer_parts),
            )
        else:
            flush()
    return merged


def _split_long_text(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    paragraphs = [part for part in re.split(r"\n+", text) if part.strip()]
    parts: list[str] = []
    buffer: list[str] = []
    length = 0
    for paragraph in paragraphs:
        if buffer and length + len(paragraph) > MAX_CHUNK_CHARS:
            parts.append("\n".join(buffer))
            buffer = []
            length = 0
        buffer.append(paragraph)
        length += len(paragraph)
    if buffer:
        parts.append("\n".join(buffer))
    return parts


# ---------------------------------------------------------------------------
# 标题式素材（docx / txt）


def _segments_from_headings(text: str) -> list[_Segment]:
    blocks = [block for block in re.split(r"\n{2,}", text) if block.strip()]
    sections: list[tuple[str, list[str]]] = []
    current_heading = ""
    current_parts: list[str] = []
    has_heading = False
    for block in blocks:
        match = _HEADING_RE.match(block.strip())
        if match:
            has_heading = True
            if current_parts:
                sections.append((current_heading, current_parts))
            current_heading = match.group(2)
            current_parts = []
        else:
            current_parts.append(block.strip())
    if current_parts:
        sections.append((current_heading, current_parts))

    segments: list[_Segment] = []
    if not has_heading:
        for part in _split_long_text("\n\n".join(part for _, parts in sections for part in parts)):
            segments.append(_Segment(text=part, unit_start=0, unit_end=0))
        return segments
    for heading, parts in sections:
        body = "\n\n".join(parts)
        for part in _split_long_text(body):
            segments.append(_Segment(text=part, unit_start=0, unit_end=0, heading=heading))
    return segments


# ---------------------------------------------------------------------------
# chunk 组装


def _overview_chunk(
    source: ParsedSourceFile,
    course_id: str,
    base_metadata: dict[str, str],
    segments: list[_Segment],
    enrichment: CourseEnrichment | None,
) -> RagChunk:
    summary = enrichment.summary.strip() if enrichment is not None else ""
    first_body = segments[0].text if segments else ""
    lines = [
        f"课程：{base_metadata['course_name']}",
        "知识类型：培训课程",
        f"课程体系：{base_metadata['course_series']}" if base_metadata["course_series"] else "",
        f"适用对象：{base_metadata['audience']}",
        f"课程摘要：{summary}" if summary else "",
        "",
        first_body[:_OVERVIEW_BODY_MAX_CHARS],
    ]
    metadata: dict[str, object] = {**base_metadata, "summary": summary}
    if enrichment is not None and enrichment.sales_stages:
        metadata["sales_stages"] = list(enrichment.sales_stages)
    return RagChunk(
        chunk_id=f"course__{course_id}__overview",
        chunk_type="training_course",
        text="\n".join(line for line in lines if line != ""),
        metadata=metadata,
        citations=[_citation(source, None)],
        source_file=source.source_path,
    )


def _segment_chunk(
    source: ParsedSourceFile,
    course_id: str,
    base_metadata: dict[str, str],
    segment: _Segment,
    index: int,
    unit_label: str,
) -> RagChunk:
    metadata: dict[str, object] = dict(base_metadata)
    location_line = ""
    if unit_label == "slide":
        metadata["slide_start"] = segment.unit_start
        metadata["slide_end"] = segment.unit_end
        location_line = _format_range("页码", segment.unit_start, segment.unit_end)
    elif unit_label == "page":
        metadata["page_start"] = segment.unit_start
        metadata["page_end"] = segment.unit_end
        location_line = _format_range("页码", segment.unit_start, segment.unit_end)
    elif segment.heading:
        metadata["heading"] = segment.heading
        location_line = f"章节：{segment.heading}"

    goals = _extract_goals_for_metadata(segment.text)
    if goals:
        metadata["teaching_goals"] = goals

    lines = [
        f"课程：{base_metadata['course_name']}",
        "知识类型：培训课程",
        location_line,
        "",
        segment.text,
    ]
    return RagChunk(
        chunk_id=f"course__{course_id}__{index:04d}",
        chunk_type="training_course",
        text="\n".join(line for line in lines if line != ""),
        metadata=metadata,
        citations=[_citation(source, segment, unit_label)],
        source_file=source.source_path,
    )


def _extract_goals_for_metadata(text: str) -> list[str]:
    goals: list[str] = []
    for match in re.finditer(
        r"^[•·◆▪○\-\*\s]*教学目标[：:]\s*(.+)$", text, re.MULTILINE
    ):
        goals.extend(_split_goal_values(match.group(1)))
    return goals


def _format_range(label: str, start: int, end: int) -> str:
    if start == end:
        return f"{label}：第 {start} 页"
    return f"{label}：第 {start}-{end} 页"


def _citation(
    source: ParsedSourceFile,
    segment: _Segment | None,
    unit_label: str = "",
) -> EvidenceRef:
    locator: dict[str, object] = {}
    quote = ""
    if segment is not None:
        quote = segment.text.strip().splitlines()[0][:_QUOTE_MAX_CHARS] if segment.text.strip() else ""
        if unit_label == "slide":
            locator = {"slide_start": segment.unit_start, "slide_end": segment.unit_end}
        elif unit_label == "page":
            locator = {"page_start": segment.unit_start, "page_end": segment.unit_end}
        elif segment.heading:
            locator = {"heading": segment.heading}
    return EvidenceRef(
        source_id=source.source_id,
        filename=source.filename,
        source_type=source.source_type,
        source_path=source.source_path,
        quote=quote,
        locator=locator,
        locator_confidence="exact",
    )
