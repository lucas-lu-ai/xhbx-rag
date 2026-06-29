from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from .models import RagChunk


class ChunkLoadError(ValueError):
    """Raised when chunks.jsonl cannot be loaded."""


def chunk_text_hash(text: str) -> str:
    normalized = text.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_chunks_jsonl(path: Path) -> list[RagChunk]:
    chunks: list[RagChunk] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ChunkLoadError(f"找不到 chunks.jsonl: {path}") from exc

    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            chunks.append(RagChunk.model_validate(data))
        except json.JSONDecodeError as exc:
            raise ChunkLoadError(f"chunks.jsonl 第 {line_no} 行 JSON 解析失败") from exc
        except ValidationError as exc:
            raise ChunkLoadError(f"chunks.jsonl 第 {line_no} 行字段校验失败: {exc}") from exc
    return chunks
