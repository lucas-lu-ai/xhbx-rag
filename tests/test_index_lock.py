import threading
from pathlib import Path

from xhbx_rag.index_lock import collection_write_lock


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
