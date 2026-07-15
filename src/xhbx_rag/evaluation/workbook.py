from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKBOOK_SCRIPT = REPO_ROOT / "scripts" / "evaluation_workbook.mjs"


class WorkbookAdapter:
    def __init__(
        self,
        run_dir: Path,
        *,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.run_dir = Path(run_dir).resolve()
        self._env = env if env is not None else os.environ

    def extract(self, input_path: Path, output_path: Path) -> Path:
        self._run(
            "extract",
            "--input",
            str(Path(input_path).resolve()),
            "--output",
            str(Path(output_path).resolve()),
        )
        return Path(output_path)

    def backfill(
        self,
        input_path: Path,
        payload_path: Path,
        output_path: Path,
    ) -> Path:
        self._run(
            "backfill",
            "--input",
            str(Path(input_path).resolve()),
            "--payload",
            str(Path(payload_path).resolve()),
            "--output",
            str(Path(output_path).resolve()),
        )
        return Path(output_path)

    def verify(
        self,
        input_path: Path,
        snapshot_path: Path,
        output_path: Path,
        preview_dir: Path,
    ) -> Path:
        self._run(
            "verify",
            "--input",
            str(Path(input_path).resolve()),
            "--snapshot",
            str(Path(snapshot_path).resolve()),
            "--output",
            str(Path(output_path).resolve()),
            "--preview-dir",
            str(Path(preview_dir).resolve()),
        )
        return Path(output_path)

    def _run(self, *arguments: str) -> None:
        node_bin, node_modules = self._configured_runtime()
        adapter_dir = self.run_dir / ".workbook_adapter"
        adapter_dir.mkdir(parents=True, exist_ok=True)
        copied_script = adapter_dir / WORKBOOK_SCRIPT.name
        if not WORKBOOK_SCRIPT.is_file():
            raise RuntimeError(f"找不到工作簿脚本：{WORKBOOK_SCRIPT}")
        shutil.copy2(WORKBOOK_SCRIPT, copied_script)
        self._ensure_node_modules_link(adapter_dir / "node_modules", node_modules)

        process_env = os.environ.copy()
        process_env.update(self._env)
        completed = subprocess.run(
            [str(node_bin), str(copied_script), *arguments],
            cwd=adapter_dir,
            env=process_env,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            if detail:
                raise RuntimeError(f"工作簿处理失败：{detail}")
            raise RuntimeError(f"工作簿处理失败，退出码：{completed.returncode}")

    def _configured_runtime(self) -> tuple[Path, Path]:
        node_value = self._env.get("EVALUATION_NODE_BIN", "").strip()
        if not node_value:
            raise RuntimeError(
                "未配置 EVALUATION_NODE_BIN，请设置 bundled Node 可执行文件路径"
            )
        modules_value = self._env.get(
            "EVALUATION_ARTIFACT_NODE_MODULES",
            "",
        ).strip()
        if not modules_value:
            raise RuntimeError(
                "未配置 EVALUATION_ARTIFACT_NODE_MODULES，"
                "请设置 bundled node_modules 目录路径"
            )

        node_bin = Path(node_value)
        node_modules = Path(modules_value)
        if not node_bin.is_file() or not os.access(node_bin, os.X_OK):
            raise RuntimeError(f"EVALUATION_NODE_BIN 不是可执行文件：{node_bin}")
        if not node_modules.is_dir():
            raise RuntimeError(
                "EVALUATION_ARTIFACT_NODE_MODULES 不是有效目录："
                f"{node_modules}"
            )
        return node_bin, node_modules

    @staticmethod
    def _ensure_node_modules_link(link_path: Path, target: Path) -> None:
        if link_path.is_symlink():
            if link_path.resolve() == target.resolve():
                return
            raise RuntimeError(f"node_modules 软链接指向了其他目录：{link_path}")
        if link_path.exists():
            raise RuntimeError(f"node_modules 路径已存在且不是软链接：{link_path}")
        link_path.symlink_to(target, target_is_directory=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="评测工作簿适配器")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    extract_parser = subparsers.add_parser("extract", help="抽取评测集")
    extract_parser.add_argument("--input", required=True, type=Path)
    extract_parser.add_argument("--output", required=True, type=Path)

    backfill_parser = subparsers.add_parser("backfill", help="回填评测结果")
    backfill_parser.add_argument("--input", required=True, type=Path)
    backfill_parser.add_argument("--payload", required=True, type=Path)
    backfill_parser.add_argument("--output", required=True, type=Path)

    verify_parser = subparsers.add_parser("verify", help="验证评测工作簿")
    verify_parser.add_argument("--input", required=True, type=Path)
    verify_parser.add_argument("--snapshot", required=True, type=Path)
    verify_parser.add_argument("--output", required=True, type=Path)
    verify_parser.add_argument("--preview-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    adapter = WorkbookAdapter(args.output.parent)
    try:
        if args.mode == "extract":
            adapter.extract(args.input, args.output)
        elif args.mode == "backfill":
            adapter.backfill(args.input, args.payload, args.output)
        else:
            adapter.verify(
                args.input,
                args.snapshot,
                args.output,
                args.preview_dir,
            )
    except RuntimeError as exc:
        print(f"工作簿适配器错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
