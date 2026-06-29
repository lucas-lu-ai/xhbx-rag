from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .chunk_builder import build_chunks
from .normalizer import normalize_case
from .parser import ParseFatalError, parse_inputs
from .report import build_parse_report
from .writer import write_outputs


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xhbx-rag",
        description="解析销售洞察文件并生成 RAG 入库前产物",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    parse_parser = subparsers.add_parser("parse", help="解析 case.sales_insights.json")
    parse_parser.add_argument("--insights", required=True, type=Path)
    parse_parser.add_argument("--playbook", type=Path, default=None)
    parse_parser.add_argument("--out", required=True, type=Path)
    return parser


def _write_fatal_report(
    out_dir: Path,
    insights_path: Path,
    playbook_path: Path | None,
    error: str,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "input_files": {
            "insights": str(insights_path),
            "playbook": str(playbook_path) if playbook_path is not None else None,
        },
        "output_files": {"report": str(out_dir / "parse_report.json")},
        "case_name": None,
        "counts": {},
        "warnings": [],
        "errors": [error],
    }
    (out_dir / "parse_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _cmd_parse(args: argparse.Namespace) -> int:
    try:
        parsed = parse_inputs(args.insights, args.playbook)
        knowledge = normalize_case(parsed)
        chunks = build_chunks(knowledge)
        report = build_parse_report(
            args.insights,
            args.playbook,
            args.out,
            knowledge,
            chunks,
            parsed.warnings,
        )
        write_outputs(args.out, knowledge, chunks, report)
    except ParseFatalError as exc:
        _write_fatal_report(args.out, args.insights, args.playbook, str(exc))
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "parse":
        return _cmd_parse(args)
    parser.error(f"未知命令: {args.command}")
    return 2
