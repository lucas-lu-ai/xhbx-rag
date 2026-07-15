from __future__ import annotations

import argparse
import errno
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKBOOK_SCRIPT = REPO_ROOT / "scripts" / "evaluation_workbook.mjs"
_PERSISTENCE_ERROR_CODE_PATTERN = re.compile(
    r"\b(?:ENOSPC|EDQUOT|EROFS|EIO)\b",
    re.IGNORECASE,
)
_PERSISTENCE_ERROR_PHRASES = (
    "no space left",
    "disk quota exceeded",
    "quota exceeded",
    "read-only file system",
    "input/output error",
    "i/o error",
)
_PERSISTENCE_ERRNOS = frozenset(
    value
    for name in ("ENOSPC", "EDQUOT", "EROFS", "EIO")
    if (value := getattr(errno, name, None)) is not None
)


class WorkbookAdapterPersistenceError(OSError):
    """Node 工作簿进程报告无法继续读取或耐久写入。"""


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
        try:
            shutil.copy2(WORKBOOK_SCRIPT, copied_script)
        except OSError as exc:
            if _is_persistence_os_error(exc):
                raise WorkbookAdapterPersistenceError(
                    "工作簿持久化失败：复制工作簿脚本："
                    f"{WORKBOOK_SCRIPT} -> {copied_script}：{exc}"
                ) from exc
            raise RuntimeError(
                "复制工作簿脚本失败："
                f"{WORKBOOK_SCRIPT} -> {copied_script}：{exc}"
            ) from exc
        self._ensure_node_modules_link(adapter_dir / "node_modules", node_modules)

        process_env = os.environ.copy()
        process_env.update(self._env)
        try:
            completed = subprocess.run(
                [str(node_bin), str(copied_script), *arguments],
                cwd=adapter_dir,
                env=process_env,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            if _is_persistence_os_error(exc):
                raise WorkbookAdapterPersistenceError(
                    "工作簿持久化失败：启动 Node 工作簿进程："
                    f"{node_bin}（工作目录：{adapter_dir}）：{exc}"
                ) from exc
            raise RuntimeError(
                "启动 Node 工作簿进程失败："
                f"{node_bin}（工作目录：{adapter_dir}）：{exc}"
            ) from exc
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            stdout = completed.stdout.strip()
            combined_detail = "\n".join(
                value for value in (stderr, stdout) if value
            )
            if _is_persistence_failure(combined_detail):
                detail = combined_detail or f"退出码：{completed.returncode}"
                raise WorkbookAdapterPersistenceError(
                    f"工作簿持久化失败：{detail}"
                )
            detail = stderr or stdout
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

        node_bin = Path(node_value).expanduser().resolve()
        node_modules = Path(modules_value).expanduser().resolve()
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
        try:
            link_path.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            if _is_persistence_os_error(exc):
                raise WorkbookAdapterPersistenceError(
                    "工作簿持久化失败：创建 node_modules 软链接："
                    f"{link_path} -> {target}：{exc}"
                ) from exc
            raise RuntimeError(
                "创建 node_modules 软链接失败："
                f"{link_path} -> {target}：{exc}"
            ) from exc


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


def _is_persistence_failure(detail: str) -> bool:
    if _PERSISTENCE_ERROR_CODE_PATTERN.search(detail):
        return True
    normalized = detail.casefold()
    return any(phrase in normalized for phrase in _PERSISTENCE_ERROR_PHRASES)


def _is_persistence_os_error(exc: OSError) -> bool:
    if exc.errno in _PERSISTENCE_ERRNOS:
        return True
    return _is_persistence_failure(str(exc))


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
    except (RuntimeError, WorkbookAdapterPersistenceError) as exc:
        print(f"工作簿适配器错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
