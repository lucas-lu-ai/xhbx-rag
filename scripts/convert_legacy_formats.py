"""批量把 doc/ppt/wps 老格式转换为 docx/pptx（一次性预处理，不进核心流水线）。

依赖本机 LibreOffice（`soffice` 命令）。转换产物落在原文件同目录，
与原文件并存；课程管线只认 docx/pptx，转换后即可被 `parse-course` 扫描。

用法：
    uv run python scripts/convert_legacy_formats.py --dir "data/新华培训数据" --dry-run
    uv run python scripts/convert_legacy_formats.py --dir "data/新华培训数据"
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_TARGET_FORMAT_BY_SUFFIX = {
    ".doc": "docx",
    ".wps": "docx",
    ".ppt": "pptx",
}
_SOFFICE_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class ConversionPlan:
    source: Path
    target: Path
    target_format: str


def build_conversion_plans(root: Path) -> tuple[list[ConversionPlan], list[str]]:
    """扫描目录生成转换计划；目标文件已存在或临时文件则跳过。"""
    plans: list[ConversionPlan] = []
    skipped: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith(("~$", ".")):
            continue
        target_format = _TARGET_FORMAT_BY_SUFFIX.get(path.suffix.lower())
        if target_format is None:
            continue
        target = path.with_suffix(f".{target_format}")
        if target.exists():
            skipped.append(f"{path.relative_to(root)}（目标已存在）")
            continue
        plans.append(
            ConversionPlan(source=path, target=target, target_format=target_format)
        )
    return plans, skipped


def convert(plan: ConversionPlan) -> str | None:
    """执行单个转换，成功返回 None，失败返回错误描述。"""
    result = subprocess.run(
        [
            "soffice",
            "--headless",
            "--convert-to",
            plan.target_format,
            "--outdir",
            str(plan.source.parent),
            str(plan.source),
        ],
        capture_output=True,
        text=True,
        timeout=_SOFFICE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        return result.stderr.strip() or f"soffice 退出码 {result.returncode}"
    if not plan.target.exists():
        return "soffice 执行成功但未产出目标文件"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量转换 doc/ppt/wps 老格式")
    parser.add_argument("--dir", required=True, type=Path, help="待扫描的根目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不执行转换")
    args = parser.parse_args(argv)

    if not args.dir.is_dir():
        print(f"目录不存在: {args.dir}", file=sys.stderr)
        return 1

    plans, skipped = build_conversion_plans(args.dir)
    summary = {
        "planned": len(plans),
        "skipped": len(skipped),
        "converted": 0,
        "failed": [],
    }

    if args.dry_run:
        for plan in plans:
            print(f"[计划] {plan.source} -> {plan.target.name}")
        for item in skipped:
            print(f"[跳过] {item}")
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    if shutil.which("soffice") is None:
        print("未找到 soffice 命令，请先安装 LibreOffice", file=sys.stderr)
        return 1

    for index, plan in enumerate(plans, start=1):
        print(f"[{index}/{len(plans)}] {plan.source}")
        try:
            error = convert(plan)
        except subprocess.TimeoutExpired:
            error = f"转换超时（>{_SOFFICE_TIMEOUT_SECONDS}s）"
        except Exception as exc:  # noqa: BLE001 - 单文件失败不中断批量
            error = repr(exc)
        if error is None:
            summary["converted"] += 1
        else:
            summary["failed"].append({"path": str(plan.source), "error": error})
            print(f"  失败: {error}", file=sys.stderr)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["failed"] else 2


if __name__ == "__main__":
    sys.exit(main())
