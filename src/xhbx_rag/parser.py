from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .models import CaseSalesInsightsSource


TOP_LEVEL_LIST_FIELDS = (
    "customer_journey",
    "strategies",
    "scripts",
    "objection_handling",
)


class ParseFatalError(ValueError):
    """Raised when source files cannot be parsed into a usable case."""


@dataclass(frozen=True)
class ParsedInputs:
    source: CaseSalesInsightsSource
    insights_path: Path
    playbook_path: Path | None
    warnings: list[str]


def _read_json_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ParseFatalError(f"找不到 case.sales_insights.json: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ParseFatalError(f"JSON 解析失败: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ParseFatalError("case.sales_insights.json 顶层必须是 JSON object")
    return data


def _playbook_warnings(playbook_path: Path | None, case_name: str) -> list[str]:
    if playbook_path is None:
        return ["未提供 case.sales_playbook.md"]
    if not playbook_path.exists():
        return [f"case.sales_playbook.md 不存在: {playbook_path}"]
    text = playbook_path.read_text(encoding="utf-8")
    first_heading = next(
        (
            line.removeprefix("#").strip()
            for line in text.splitlines()
            if line.startswith("#")
        ),
        "",
    )
    if first_heading and case_name not in first_heading:
        return [f"playbook 标题未包含 case_name: {case_name}"]
    return []


def parse_inputs(insights_path: Path, playbook_path: Path | None) -> ParsedInputs:
    data = _read_json_object(insights_path)
    warnings: list[str] = []

    if not str(data.get("case_name", "")).strip():
        raise ParseFatalError("case.sales_insights.json 缺少必需字段 case_name")

    for field_name in TOP_LEVEL_LIST_FIELDS:
        if field_name not in data:
            warnings.append(f"缺少 {field_name}，已按空列表处理")

    try:
        source = CaseSalesInsightsSource.model_validate(data)
    except ValidationError as exc:
        raise ParseFatalError(f"case.sales_insights.json 字段校验失败: {exc}") from exc

    warnings.extend(_playbook_warnings(playbook_path, source.case_name))
    return ParsedInputs(
        source=source,
        insights_path=insights_path,
        playbook_path=playbook_path,
        warnings=warnings,
    )
