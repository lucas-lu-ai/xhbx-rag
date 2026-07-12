from __future__ import annotations

import inspect
import os
import re
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal
from zipfile import BadZipFile, ZipFile, ZipInfo

from xhbx_rag.source_loader import SUPPORTED_EXTENSIONS


IngestionTarget = Literal["case", "course"]


class UploadValidationError(ValueError):
    """上传格式、ZIP 安全或输入映射无效。"""


@dataclass(frozen=True)
class IngestionLimits:
    max_upload_bytes: int = 536_870_912
    max_zip_entries: int = 2_000
    max_extracted_bytes: int = 2_147_483_648
    max_entry_bytes: int = 536_870_912
    max_compression_ratio: float = 100.0
    max_path_chars: int = 512


@dataclass(frozen=True)
class PreflightItem:
    item_index: int
    unit_key: str
    display_name: str
    relative_paths: tuple[str, ...]
    document_count: int


@dataclass(frozen=True)
class PreflightResult:
    source_name: str
    source_kind: Literal["file", "zip"]
    target: IngestionTarget
    items: tuple[PreflightItem, ...]
    ignored_entries: tuple[str, ...] = field(default_factory=tuple)

    @property
    def document_total(self) -> int:
        return sum(item.document_count for item in self.items)

    @property
    def ignored_total(self) -> int:
        return len(self.ignored_entries)


async def save_upload_file(
    upload: object,
    destination: Path,
    *,
    max_bytes: int,
    chunk_bytes: int = 1_048_576,
) -> int:
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes 必须大于 0")
    read = getattr(upload, "read", None)
    if not callable(read):
        raise TypeError("upload 必须提供 read()")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.uploading")
    total = 0
    file_descriptor: int | None = None
    owns_temporary = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            file_descriptor = os.open(temporary, flags, 0o600)
        except FileExistsError as exc:
            raise UploadValidationError("同一目标的上传正在进行") from exc
        owns_temporary = True
        output_file = os.fdopen(file_descriptor, "wb")
        file_descriptor = None
        with output_file as output:
            while True:
                chunk = read(chunk_bytes)
                if inspect.isawaitable(chunk):
                    chunk = await chunk
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise TypeError("upload.read() 必须返回 bytes")
                total += len(chunk)
                if total > max_bytes:
                    raise UploadValidationError("上传文件超过大小限制")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(destination)
    except BaseException:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if owns_temporary:
            temporary.unlink(missing_ok=True)
        raise
    return total


def preflight_upload(
    source_path: Path,
    *,
    target: IngestionTarget,
    limits: IngestionLimits,
) -> PreflightResult:
    if target not in ("case", "course"):
        raise UploadValidationError(f"不支持的入库目标: {target}")
    if not source_path.is_file():
        raise UploadValidationError(f"上传文件不存在: {source_path.name}")
    if source_path.stat().st_size > limits.max_upload_bytes:
        raise UploadValidationError("上传文件超过大小限制")
    if source_path.suffix.lower() == ".zip":
        return _preflight_zip(source_path, target=target, limits=limits)
    return _preflight_file(source_path, target=target)


def materialize_attempt_inputs(
    source_path: Path,
    preflight: PreflightResult,
    attempt_dir: Path,
    *,
    limits: IngestionLimits,
) -> dict[int, Path]:
    extracted_root = attempt_dir / "extracted"
    _remove_path(extracted_root)
    items_root = extracted_root / "items"
    items_root.mkdir(parents=True, exist_ok=True)

    try:
        if preflight.source_kind == "zip":
            return _materialize_zip_inputs(
                source_path,
                preflight,
                extracted_root=extracted_root,
                items_root=items_root,
                limits=limits,
            )
        return _materialize_file_input(
            source_path,
            preflight,
            extracted_root=extracted_root,
            items_root=items_root,
            limits=limits,
        )
    except BaseException:
        _remove_path(extracted_root)
        raise


def clear_attempt_workspaces(job_dir: Path) -> None:
    attempts_dir = job_dir / "attempts"
    _remove_path(attempts_dir)
    attempts_dir.mkdir(parents=True, exist_ok=True)


def delete_job_workspace(job_dir: Path) -> None:
    _remove_path(job_dir)


def _preflight_file(source_path: Path, *, target: IngestionTarget) -> PreflightResult:
    if source_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise UploadValidationError("上传中没有支持的文档")
    item = PreflightItem(
        item_index=1,
        unit_key=source_path.name,
        display_name=source_path.stem,
        relative_paths=(source_path.name,),
        document_count=1,
    )
    return PreflightResult(
        source_name=source_path.name,
        source_kind="file",
        target=target,
        items=(item,),
    )


def _preflight_zip(
    source_path: Path,
    *,
    target: IngestionTarget,
    limits: IngestionLimits,
) -> PreflightResult:
    try:
        with ZipFile(source_path) as archive:
            entries = _validated_zip_entries(archive.infolist(), limits=limits)
    except BadZipFile as exc:
        raise UploadValidationError("ZIP 文件无效") from exc

    return _zip_preflight_result(
        source_name=source_path.name,
        target=target,
        entries=entries,
    )


def _zip_preflight_result(
    *,
    source_name: str,
    target: IngestionTarget,
    entries: list[tuple[str, ZipInfo]],
) -> PreflightResult:
    supported: list[str] = []
    ignored: list[str] = []
    for normalized, info in entries:
        if _zip_info_is_dir(info):
            ignored.append(normalized)
        elif _is_ignored_path(normalized):
            ignored.append(normalized)
        elif PurePosixPath(normalized).suffix.lower() in SUPPORTED_EXTENSIONS:
            supported.append(normalized)
        else:
            ignored.append(normalized)

    supported.sort()
    ignored.sort()
    if not supported:
        raise UploadValidationError("上传中没有支持的文档")
    items = (
        _map_case_items(supported, source_stem=Path(source_name).stem)
        if target == "case"
        else _map_course_items(supported)
    )
    return PreflightResult(
        source_name=source_name,
        source_kind="zip",
        target=target,
        items=items,
        ignored_entries=tuple(ignored),
    )


def _normalize_zip_path(raw_path: str) -> str:
    return raw_path.replace("\\", "/")


def _validated_zip_entries(
    infos: list[ZipInfo],
    *,
    limits: IngestionLimits,
) -> list[tuple[str, ZipInfo]]:
    if len(infos) > limits.max_zip_entries:
        raise UploadValidationError("ZIP 文件数量超过限制")

    entries: list[tuple[str, ZipInfo]] = []
    seen_paths: dict[str, bool] = {}
    extracted_total = 0
    for info in infos:
        normalized = _normalize_zip_path(info.filename)
        _validate_zip_path(normalized, limits=limits)
        is_directory = _zip_info_is_dir(info)
        path_key = normalized.removesuffix("/")
        if path_key in seen_paths:
            raise UploadValidationError(f"ZIP 路径冲突: {normalized}")
        for parent in PurePosixPath(path_key).parents:
            parent_key = str(parent)
            if parent_key == ".":
                break
            if parent_key in seen_paths and not seen_paths[parent_key]:
                raise UploadValidationError(f"ZIP 路径冲突: {normalized}")
        if not is_directory and any(
            existing.startswith(f"{path_key}/") for existing in seen_paths
        ):
            raise UploadValidationError(f"ZIP 路径冲突: {normalized}")
        seen_paths[path_key] = is_directory

        if info.flag_bits & 0x1:
            raise UploadValidationError(f"ZIP 中不允许加密条目: {normalized}")
        mode = (info.external_attr >> 16) & 0xFFFF
        mode_type = stat.S_IFMT(mode)
        if mode_type == stat.S_IFLNK:
            raise UploadValidationError(f"ZIP 中不允许符号链接: {normalized}")
        if mode_type not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise UploadValidationError(f"ZIP 中不允许非常规类型: {normalized}")
        if info.file_size > limits.max_entry_bytes:
            raise UploadValidationError(f"ZIP 单个条目过大: {normalized}")

        extracted_total += info.file_size
        if extracted_total > limits.max_extracted_bytes:
            raise UploadValidationError("ZIP 解压后总大小超过限制")

        if info.file_size:
            if info.compress_size == 0:
                raise UploadValidationError(f"ZIP 压缩比超过限制: {normalized}")
            ratio = info.file_size / info.compress_size
            if ratio > limits.max_compression_ratio:
                raise UploadValidationError(f"ZIP 压缩比超过限制: {normalized}")
        entries.append((normalized, info))
    return entries


def _validate_zip_path(path: str, *, limits: IngestionLimits) -> None:
    if len(path) > limits.max_path_chars:
        raise UploadValidationError(f"ZIP 路径过长: {path}")
    parts = path.removesuffix("/").split("/")
    if (
        not path
        or "\x00" in path
        or path.startswith("/")
        or any(
            part in ("", ".", "..") or re.match(r"^[A-Za-z]:", part)
            for part in parts
        )
    ):
        raise UploadValidationError(f"ZIP 路径不安全: {path}")


def _zip_info_is_dir(info: ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return info.is_dir() or stat.S_ISDIR(mode)


def _is_ignored_path(relative_path: str) -> bool:
    parts = PurePosixPath(relative_path).parts
    return (
        any(part == "__MACOSX" or part.startswith(".") for part in parts)
        or PurePosixPath(relative_path).name.startswith("~$")
    )


def _map_case_items(
    relative_paths: list[str],
    *,
    source_stem: str,
) -> tuple[PreflightItem, ...]:
    grouped: dict[str, list[str]] = {}
    original_names: dict[str, str] = {}
    safe_name_owners: dict[str, str] = {}
    for relative_path in relative_paths:
        parts = PurePosixPath(relative_path).parts
        unit_key = parts[0] if len(parts) > 1 else "__root__"
        normalized_name = unit_key.strip()
        if len(parts) > 1 and normalized_name == "__root__":
            raise UploadValidationError("案例名称冲突: __root__ 为保留名称")
        previous = original_names.get(normalized_name)
        if previous is not None and previous != unit_key:
            raise UploadValidationError(f"案例名称冲突: {previous} / {unit_key}")
        original_names[normalized_name] = unit_key
        if len(parts) > 1:
            safe_name = _safe_case_name(normalized_name)
            safe_owner = safe_name_owners.get(safe_name)
            if safe_owner is not None and safe_owner != normalized_name:
                raise UploadValidationError(
                    f"案例名称冲突: {safe_owner} / {normalized_name}"
                )
            safe_name_owners[safe_name] = normalized_name
        grouped.setdefault(normalized_name, []).append(relative_path)

    if (
        "__root__" in grouped
        and _safe_case_name(source_stem) in safe_name_owners
    ):
        raise UploadValidationError(f"案例名称冲突: {source_stem}")

    items: list[PreflightItem] = []
    for index, unit_key in enumerate(sorted(grouped), start=1):
        paths = tuple(grouped[unit_key])
        display_name = source_stem if unit_key == "__root__" else unit_key
        items.append(
            PreflightItem(
                item_index=index,
                unit_key=unit_key,
                display_name=display_name,
                relative_paths=paths,
                document_count=len(paths),
            )
        )
    return tuple(items)


def _safe_case_name(value: str) -> str:
    safe = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value).strip("._ ")
    return safe or "case"


def _map_course_items(relative_paths: list[str]) -> tuple[PreflightItem, ...]:
    return tuple(
        PreflightItem(
            item_index=index,
            unit_key=relative_path,
            display_name=PurePosixPath(relative_path).stem,
            relative_paths=(relative_path,),
            document_count=1,
        )
        for index, relative_path in enumerate(relative_paths, start=1)
    )


def _materialize_file_input(
    source_path: Path,
    preflight: PreflightResult,
    *,
    extracted_root: Path,
    items_root: Path,
    limits: IngestionLimits,
) -> dict[int, Path]:
    if not source_path.is_file():
        raise UploadValidationError(f"上传文件不存在: {source_path.name}")
    size = source_path.stat().st_size
    if size > limits.max_upload_bytes or size > limits.max_entry_bytes:
        raise UploadValidationError("上传文件超过大小限制")
    if size > limits.max_extracted_bytes:
        raise UploadValidationError("文档总大小超过限制")
    if len(preflight.items) != 1:
        raise UploadValidationError("单文件预检映射无效")

    item = preflight.items[0]
    item_dir = _new_item_dir(items_root, item.item_index)
    destination = item_dir / source_path.name
    _verify_destination(destination, extracted_root=extracted_root, item_dir=item_dir)
    with source_path.open("rb") as input_file, destination.open("xb") as output:
        shutil.copyfileobj(input_file, output, length=1_048_576)
    return {item.item_index: item_dir}


def _materialize_zip_inputs(
    source_path: Path,
    preflight: PreflightResult,
    *,
    extracted_root: Path,
    items_root: Path,
    limits: IngestionLimits,
) -> dict[int, Path]:
    if source_path.stat().st_size > limits.max_upload_bytes:
        raise UploadValidationError("上传文件超过大小限制")
    try:
        with ZipFile(source_path) as archive:
            entries = _validated_zip_entries(archive.infolist(), limits=limits)
            try:
                current_preflight = _zip_preflight_result(
                    source_name=source_path.name,
                    target=preflight.target,
                    entries=entries,
                )
            except UploadValidationError as exc:
                raise UploadValidationError(
                    "ZIP 内容与预检结果不一致"
                ) from exc
            if current_preflight != preflight:
                raise UploadValidationError("ZIP 内容与预检结果不一致")
            entries_by_path = {path: info for path, info in entries}
            result: dict[int, Path] = {}
            actual_total = 0
            for item in preflight.items:
                if item.item_index in result:
                    raise UploadValidationError("预检项序号重复")
                item_dir = _new_item_dir(items_root, item.item_index)
                result[item.item_index] = item_dir
                for relative_path in item.relative_paths:
                    info = entries_by_path.get(relative_path)
                    if info is None or _zip_info_is_dir(info):
                        raise UploadValidationError(
                            f"ZIP 内容与预检结果不一致: {relative_path}"
                        )
                    if preflight.target == "course":
                        destination_relative = PurePosixPath(relative_path)
                    else:
                        destination_relative = _case_destination_relative(
                            relative_path,
                            unit_key=item.unit_key,
                        )
                    destination = item_dir.joinpath(*destination_relative.parts)
                    _verify_destination(
                        destination,
                        extracted_root=extracted_root,
                        item_dir=item_dir,
                    )
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    entry_size = 0
                    with archive.open(info) as input_file, destination.open("xb") as output:
                        while True:
                            chunk = input_file.read(1_048_576)
                            if not chunk:
                                break
                            entry_size += len(chunk)
                            actual_total += len(chunk)
                            if entry_size > limits.max_entry_bytes:
                                raise UploadValidationError(
                                    f"ZIP 单个条目过大: {relative_path}"
                                )
                            if actual_total > limits.max_extracted_bytes:
                                raise UploadValidationError(
                                    "ZIP 解压后总大小超过限制"
                                )
                            output.write(chunk)
            return result
    except BadZipFile as exc:
        raise UploadValidationError("ZIP 文件无效") from exc
    except RuntimeError as exc:
        raise UploadValidationError("ZIP 条目无法解压") from exc


def _case_destination_relative(relative_path: str, *, unit_key: str) -> PurePosixPath:
    parts = PurePosixPath(relative_path).parts
    if unit_key == "__root__":
        if len(parts) != 1:
            raise UploadValidationError(f"案例根目录映射无效: {relative_path}")
        return PurePosixPath(*parts)
    if len(parts) < 2 or parts[0].strip() != unit_key:
        raise UploadValidationError(f"案例目录映射无效: {relative_path}")
    return PurePosixPath(*parts[1:])


def _new_item_dir(items_root: Path, item_index: int) -> Path:
    if item_index < 1:
        raise UploadValidationError("预检项序号无效")
    item_dir = items_root / f"item-{item_index:04d}"
    item_dir.mkdir(parents=False, exist_ok=False)
    return item_dir


def _verify_destination(
    destination: Path,
    *,
    extracted_root: Path,
    item_dir: Path,
) -> None:
    resolved_path = destination.resolve()
    if not resolved_path.is_relative_to(extracted_root.resolve()):
        raise UploadValidationError(f"ZIP 路径不安全: {destination.name}")
    if not resolved_path.is_relative_to(item_dir.resolve()):
        raise UploadValidationError(f"ZIP 路径越过项目目录: {destination.name}")


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)
