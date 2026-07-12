from __future__ import annotations

import fcntl
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def collection_write_lock(
    uri: str,
    collection_name: str,
    lock_root: Path | None = None,
) -> Iterator[None]:
    root = lock_root or Path(".local/index-locks")
    root.mkdir(parents=True, exist_ok=True)
    lock_key = hashlib.sha256(f"{uri}\0{collection_name}".encode()).hexdigest()
    lock_file = (root / f"{lock_key}.lock").open("a+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
