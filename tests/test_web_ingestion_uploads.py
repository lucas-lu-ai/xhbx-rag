import asyncio
from pathlib import Path
import stat
from zipfile import ZipFile, ZipInfo

import pytest

from xhbx_rag.web.ingestion_uploads import (
    IngestionLimits,
    UploadValidationError,
    clear_attempt_workspaces,
    delete_job_workspace,
    materialize_attempt_inputs,
    preflight_upload,
    save_upload_file,
)


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def _mark_first_zip_entry_encrypted(path: Path) -> None:
    data = bytearray(path.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        header = data.index(signature)
        start = header + flag_offset
        flags = int.from_bytes(data[start : start + 2], "little") | 0x1
        data[start : start + 2] = flags.to_bytes(2, "little")
    path.write_bytes(data)


def test_case_zip_maps_root_files_and_first_level_directories(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "优秀案例.zip",
        {
            "根目录.txt": b"root",
            "王女士/第一节/讲义.txt": b"case-a",
            "李先生/需求分析.docx": b"case-b",
            "__MACOSX/._meta": b"ignored",
            "王女士/video.mp4": b"ignored",
        },
    )

    result = preflight_upload(source, target="case", limits=IngestionLimits())

    assert [(item.unit_key, item.document_count) for item in result.items] == [
        ("__root__", 1),
        ("李先生", 1),
        ("王女士", 1),
    ]
    root_item = next(item for item in result.items if item.unit_key == "__root__")
    assert root_item.display_name == "优秀案例"
    assert result.document_total == 3
    assert result.ignored_total == 2


def test_course_zip_maps_each_document_and_keeps_relative_path(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "新人课程.zip",
        {
            "新人培训/促成课.pptx": b"pptx",
            "新人培训/异议处理/讲义.pdf": b"pdf",
        },
    )

    result = preflight_upload(source, target="course", limits=IngestionLimits())

    assert [item.unit_key for item in result.items] == [
        "新人培训/促成课.pptx",
        "新人培训/异议处理/讲义.pdf",
    ]


def test_zip_ignores_hidden_temporary_and_unsupported_entries(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "course.zip",
        {
            "course.txt": b"kept",
            ".hidden/notes.txt": b"hidden-directory",
            "normal/.hidden.txt": b"hidden-file",
            "normal/~$draft.docx": b"office-temporary",
            "normal/video.mp4": b"unsupported",
        },
    )

    result = preflight_upload(source, target="course", limits=IngestionLimits())

    assert [item.unit_key for item in result.items] == ["course.txt"]
    assert result.ignored_entries == (
        ".hidden/notes.txt",
        "normal/.hidden.txt",
        "normal/video.mp4",
        "normal/~$draft.docx",
    )


def test_zip_counts_explicit_directory_entries_as_ignored(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "course.zip",
        {"course/": b"", "course/notes.txt": b"notes"},
    )

    result = preflight_upload(source, target="course", limits=IngestionLimits())

    assert result.ignored_entries == ("course/",)
    assert result.ignored_total == 1


def test_case_zip_rejects_normalized_name_collision(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "案例.zip",
        {"A/B.txt": b"a", "A /C.txt": b"b"},
    )

    with pytest.raises(UploadValidationError, match="案例名称冲突"):
        preflight_upload(source, target="case", limits=IngestionLimits())


def test_case_zip_rejects_downstream_safe_name_collision(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "cases.zip",
        {"A B/notes.txt": b"space", "A?B/notes.txt": b"question"},
    )

    with pytest.raises(UploadValidationError, match="案例名称冲突"):
        preflight_upload(source, target="case", limits=IngestionLimits())


def test_case_zip_rejects_reserved_root_unit_key(tmp_path: Path) -> None:
    source = _zip(tmp_path / "cases.zip", {"__root__/notes.txt": b"notes"})

    with pytest.raises(UploadValidationError, match="案例名称冲突"):
        preflight_upload(source, target="case", limits=IngestionLimits())


def test_case_zip_rejects_zip_stem_display_name_collision(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "cases.zip",
        {"root.txt": b"root", "cases/notes.txt": b"notes"},
    )

    with pytest.raises(UploadValidationError, match="案例名称冲突"):
        preflight_upload(source, target="case", limits=IngestionLimits())


def test_case_zip_rejects_safe_name_collision_with_zip_stem(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "A B.zip",
        {"root.txt": b"root", "A?B/notes.txt": b"notes"},
    )

    with pytest.raises(UploadValidationError, match="案例名称冲突"):
        preflight_upload(source, target="case", limits=IngestionLimits())


@pytest.mark.parametrize(
    "entry",
    [
        "../secret.txt",
        "/etc/passwd",
        "C:/secret.txt",
        "C:secret.txt",
        "safe/C:/secret.txt",
        "safe/C:secret.txt",
        "safe/../../secret.txt",
        "safe/./secret.txt",
        "safe//secret.txt",
    ],
)
def test_zip_rejects_unsafe_paths(tmp_path: Path, entry: str) -> None:
    source = _zip(tmp_path / "bad.zip", {entry: b"bad"})
    with pytest.raises(UploadValidationError, match="ZIP 路径不安全"):
        preflight_upload(source, target="course", limits=IngestionLimits())


def test_zip_rejects_symlink(tmp_path: Path) -> None:
    source = tmp_path / "link.zip"
    info = ZipInfo("link.txt")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with ZipFile(source, "w") as archive:
        archive.writestr(info, "target.txt")
    with pytest.raises(UploadValidationError, match="符号链接"):
        preflight_upload(source, target="course", limits=IngestionLimits())


def test_zip_rejects_fifo(tmp_path: Path) -> None:
    source = tmp_path / "fifo.zip"
    info = ZipInfo("fifo.txt")
    info.create_system = 3
    info.external_attr = (stat.S_IFIFO | 0o644) << 16
    with ZipFile(source, "w") as archive:
        archive.writestr(info, "fifo")

    with pytest.raises(UploadValidationError, match="非常规类型"):
        preflight_upload(source, target="course", limits=IngestionLimits())


def test_zip_rejects_encrypted_entry(tmp_path: Path) -> None:
    source = _zip(tmp_path / "encrypted.zip", {"course.txt": b"course"})
    _mark_first_zip_entry_encrypted(source)
    with ZipFile(source) as archive:
        assert archive.infolist()[0].flag_bits & 0x1

    with pytest.raises(UploadValidationError, match="加密条目"):
        preflight_upload(source, target="course", limits=IngestionLimits())


def test_zip_rejects_declared_extracted_total(tmp_path: Path) -> None:
    source = _zip(tmp_path / "large.zip", {"course.txt": b"123456"})
    limits = IngestionLimits(max_extracted_bytes=5)
    with pytest.raises(UploadValidationError, match="解压后总大小"):
        preflight_upload(source, target="course", limits=limits)


class _ChunkedUpload:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = iter(chunks)

    async def read(self, _: int) -> bytes:
        return next(self._chunks, b"")


def test_save_upload_file_replaces_temporary_file_after_fsync(tmp_path: Path) -> None:
    destination = tmp_path / "source" / "案例.zip"

    size = asyncio.run(
        save_upload_file(
            _ChunkedUpload([b"abc", b"def"]),
            destination,
            max_bytes=6,
            chunk_bytes=3,
        )
    )

    assert size == 6
    assert destination.read_bytes() == b"abcdef"
    assert not destination.with_name("案例.zip.uploading").exists()


def test_save_upload_file_unlinks_temporary_file_when_limit_exceeded(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "source" / "large.zip"

    with pytest.raises(UploadValidationError, match="大小限制"):
        asyncio.run(
            save_upload_file(
                _ChunkedUpload([b"1234", b"56"]),
                destination,
                max_bytes=5,
            )
        )

    assert not destination.exists()
    assert not destination.with_name("large.zip.uploading").exists()


def test_save_upload_file_rejects_concurrent_writer(tmp_path: Path) -> None:
    destination = tmp_path / "source/upload.zip"

    class BlockingUpload:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.finished = False

        async def read(self, _: int) -> bytes:
            if self.finished:
                return b""
            self.started.set()
            await self.release.wait()
            self.finished = True
            return b"first"

    async def run_uploads() -> None:
        first_upload = BlockingUpload()
        first = asyncio.create_task(
            save_upload_file(first_upload, destination, max_bytes=100)
        )
        await first_upload.started.wait()
        with pytest.raises(UploadValidationError, match="上传正在进行"):
            await save_upload_file(
                _ChunkedUpload([b"second"]),
                destination,
                max_bytes=100,
            )
        first_upload.release.set()
        assert await first == 5

    asyncio.run(run_uploads())

    assert destination.read_bytes() == b"first"


def test_save_upload_file_does_not_follow_existing_temporary_symlink(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "source/upload.zip"
    destination.parent.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"unchanged")
    destination.with_name("upload.zip.uploading").symlink_to(outside)

    with pytest.raises(UploadValidationError, match="上传正在进行"):
        asyncio.run(
            save_upload_file(
                _ChunkedUpload([b"malicious"]),
                destination,
                max_bytes=100,
            )
        )

    assert outside.read_bytes() == b"unchanged"


def test_zip_rejects_file_directory_prefix_collision(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "collision.zip",
        {"A/B.txt": b"file", "A/B.txt/C.txt": b"child"},
    )

    with pytest.raises(UploadValidationError, match="ZIP 路径冲突"):
        preflight_upload(source, target="case", limits=IngestionLimits())


def test_zip_treats_unix_mode_directory_as_ignored(tmp_path: Path) -> None:
    source = tmp_path / "directory.zip"
    directory = ZipInfo("fake.txt")
    directory.create_system = 3
    directory.external_attr = (stat.S_IFDIR | 0o755) << 16
    with ZipFile(source, "w") as archive:
        archive.writestr(directory, b"")
        archive.writestr("course.txt", b"course")

    result = preflight_upload(source, target="course", limits=IngestionLimits())

    assert [item.unit_key for item in result.items] == ["course.txt"]
    assert result.ignored_entries == ("fake.txt",)


def test_materialize_case_items_uses_isolated_directories(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "cases.zip",
        {
            "root.txt": b"root",
            "A/第一节/讲义.txt": b"case-a",
            "B/需求.docx": b"case-b",
        },
    )
    limits = IngestionLimits()
    preflight = preflight_upload(source, target="case", limits=limits)

    inputs = materialize_attempt_inputs(
        source,
        preflight,
        tmp_path / "attempt-1",
        limits=limits,
    )

    assert inputs == {
        1: tmp_path / "attempt-1/extracted/items/item-0001",
        2: tmp_path / "attempt-1/extracted/items/item-0002",
        3: tmp_path / "attempt-1/extracted/items/item-0003",
    }
    by_unit = {item.unit_key: inputs[item.item_index] for item in preflight.items}
    assert (by_unit["__root__"] / "root.txt").read_bytes() == b"root"
    assert (by_unit["A"] / "第一节/讲义.txt").read_bytes() == b"case-a"
    assert (by_unit["B"] / "需求.docx").read_bytes() == b"case-b"


def test_materialize_course_item_keeps_complete_relative_path(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "courses.zip",
        {"新人培训/异议处/讲义.pdf": b"pdf"},
    )
    limits = IngestionLimits()
    preflight = preflight_upload(source, target="course", limits=limits)

    inputs = materialize_attempt_inputs(
        source,
        preflight,
        tmp_path / "attempt-1",
        limits=limits,
    )

    assert (
        inputs[1] / "新人培训/异议处/讲义.pdf"
    ).read_bytes() == b"pdf"


def test_materialize_zip_rechecks_limits(tmp_path: Path) -> None:
    source = _zip(tmp_path / "course.zip", {"course.txt": b"123456"})
    preflight = preflight_upload(
        source,
        target="course",
        limits=IngestionLimits(),
    )

    with pytest.raises(UploadValidationError, match="解压后总大小"):
        materialize_attempt_inputs(
            source,
            preflight,
            tmp_path / "attempt-1",
            limits=IngestionLimits(max_extracted_bytes=5),
        )


def test_materialize_zip_rejects_changed_preflight_mapping(tmp_path: Path) -> None:
    source = _zip(tmp_path / "course.zip", {"course.txt": b"course"})
    limits = IngestionLimits()
    preflight = preflight_upload(source, target="course", limits=limits)
    _zip(
        source,
        {"course.txt": b"course", "new-course.txt": b"new"},
    )

    with pytest.raises(UploadValidationError, match="ZIP 内容与预检结果不一致"):
        materialize_attempt_inputs(
            source,
            preflight,
            tmp_path / "attempt-1",
            limits=limits,
        )

    assert not (tmp_path / "attempt-1/extracted").exists()


def test_preflight_rejects_invalid_target(tmp_path: Path) -> None:
    source = tmp_path / "course.txt"
    source.write_bytes(b"course")

    with pytest.raises(UploadValidationError, match="入库目标"):
        preflight_upload(source, target="invalid", limits=IngestionLimits())  # type: ignore[arg-type]


def test_clear_attempt_workspaces_preserves_source_and_is_idempotent(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    source = job_dir / "source/upload.zip"
    attempt_file = job_dir / "attempts/attempt-1/result.json"
    source.parent.mkdir(parents=True)
    attempt_file.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    attempt_file.write_text("result", encoding="utf-8")

    clear_attempt_workspaces(job_dir)
    clear_attempt_workspaces(job_dir)

    assert source.read_bytes() == b"source"
    assert (job_dir / "attempts").is_dir()
    assert list((job_dir / "attempts").iterdir()) == []


def test_delete_job_workspace_is_idempotent(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    (job_dir / "source").mkdir(parents=True)
    (job_dir / "source/upload.zip").write_bytes(b"source")

    delete_job_workspace(job_dir)
    delete_job_workspace(job_dir)

    assert not job_dir.exists()
