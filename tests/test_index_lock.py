import multiprocessing
import queue
import threading
from pathlib import Path
from typing import Any

import pytest

import xhbx_rag.index_lock as index_lock
from xhbx_rag.index_lock import collection_write_lock


def _hold_collection_lock(
    label: str,
    uri: str,
    collection_name: str,
    lock_root: str,
    entered: Any,
    release: Any,
) -> None:
    with collection_write_lock(uri, collection_name, lock_root=Path(lock_root)):
        entered.put(label)
        release.wait(timeout=5)


def test_collection_write_lock_serializes_same_collection(tmp_path: Path) -> None:
    entered: list[str] = []
    first_ready = threading.Event()
    release_first = threading.Event()

    def first() -> None:
        with collection_write_lock("db", "chunks", lock_root=tmp_path):
            entered.append("first")
            first_ready.set()
            release_first.wait(timeout=5)

    def second() -> None:
        first_ready.wait(timeout=5)
        with collection_write_lock("db", "chunks", lock_root=tmp_path):
            entered.append("second")

    a = threading.Thread(target=first)
    b = threading.Thread(target=second)
    a.start()
    b.start()
    first_ready.wait(timeout=5)
    assert entered == ["first"]
    release_first.set()
    a.join(timeout=5)
    b.join(timeout=5)
    assert entered == ["first", "second"]


def test_collection_write_lock_serializes_same_collection_across_processes(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    entered = context.Queue()
    release = context.Event()
    args = ("db", "chunks", str(tmp_path), entered, release)
    first = context.Process(target=_hold_collection_lock, args=("first", *args))
    second = context.Process(target=_hold_collection_lock, args=("second", *args))
    try:
        first.start()
        assert entered.get(timeout=5) == "first"
        second.start()
        with pytest.raises(queue.Empty):
            entered.get(timeout=0.3)

        release.set()
        assert entered.get(timeout=5) == "second"
        first.join(timeout=5)
        second.join(timeout=5)
        assert first.exitcode == 0
        assert second.exitcode == 0
    finally:
        release.set()
        for process in (first, second):
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)


def test_collection_write_lock_releases_after_context_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with collection_write_lock("db", "chunks", lock_root=tmp_path):
            raise RuntimeError("boom")

    with collection_write_lock("db", "chunks", lock_root=tmp_path):
        reacquired = True

    assert reacquired is True


def test_collection_write_lock_closes_file_when_unlock_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened_files: list[Any] = []
    original_open = Path.open

    def tracking_open(path: Path, *args: Any, **kwargs: Any):
        lock_file = original_open(path, *args, **kwargs)
        opened_files.append(lock_file)
        return lock_file

    def failing_unlock(_fd: int, operation: int) -> None:
        if operation == index_lock.fcntl.LOCK_UN:
            raise OSError("unlock failed")

    monkeypatch.setattr(Path, "open", tracking_open)
    monkeypatch.setattr(index_lock.fcntl, "flock", failing_unlock)

    with pytest.raises(OSError, match="unlock failed"):
        with collection_write_lock("db", "chunks", lock_root=tmp_path):
            pass

    assert opened_files[0].closed is True


def test_collection_write_lock_normalizes_local_uri_and_default_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    nested = project_root / "nested"
    (project_root / "data").mkdir(parents=True)
    nested.mkdir()
    monkeypatch.setattr(index_lock, "_PROJECT_ROOT", project_root, raising=False)
    monkeypatch.setenv("HOME", str(project_root))

    monkeypatch.chdir(project_root)
    with collection_write_lock("data/./rag.db", "chunks"):
        pass

    monkeypatch.chdir(nested)
    with collection_write_lock("../data/../data/rag.db", "chunks"):
        pass
    with collection_write_lock("~/data/rag.db", "chunks"):
        pass

    lock_files = list((project_root / ".local/index-locks").glob("*.lock"))
    assert len(lock_files) == 1
    assert not (nested / ".local/index-locks").exists()


def test_collection_write_lock_normalizes_remote_uri_without_credentials(
    tmp_path: Path,
) -> None:
    with collection_write_lock(
        "HTTPS://user:secret@EXAMPLE.com:19530/?token=secret",
        "chunks",
        lock_root=tmp_path,
    ):
        pass
    with collection_write_lock(
        "https://example.com:19530",
        "chunks",
        lock_root=tmp_path,
    ):
        pass

    lock_files = list(tmp_path.glob("*.lock"))
    assert len(lock_files) == 1
    assert "secret" not in lock_files[0].name


def test_collection_write_lock_uses_distinct_keys_for_uri_and_collection(
    tmp_path: Path,
) -> None:
    with collection_write_lock("first.db", "chunks", lock_root=tmp_path):
        pass
    with collection_write_lock("second.db", "chunks", lock_root=tmp_path):
        pass
    with collection_write_lock("first.db", "courses", lock_root=tmp_path):
        pass

    assert len(list(tmp_path.glob("*.lock"))) == 3
