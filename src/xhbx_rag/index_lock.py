from __future__ import annotations

import fcntl
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _normalize_uri(uri: str) -> str:
    normalized = uri.strip()
    if "://" not in normalized:
        return str(Path(normalized).expanduser().resolve(strict=False))

    parsed = urlsplit(normalized)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), f"{hostname}{port}", path, "", ""))


@contextmanager
def collection_write_lock(
    uri: str,
    collection_name: str,
    lock_root: Path | None = None,
) -> Iterator[None]:
    root = lock_root if lock_root is not None else _PROJECT_ROOT / ".local/index-locks"
    root.mkdir(parents=True, exist_ok=True)
    normalized_uri = _normalize_uri(uri)
    lock_key = hashlib.sha256(
        f"{normalized_uri}\0{collection_name}".encode("utf-8")
    ).hexdigest()
    lock_file = (root / f"{lock_key}.lock").open("a+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()
