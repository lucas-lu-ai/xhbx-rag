from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from xhbx_rag.web.ingestion_uploads import IngestionLimits


class ConfigError(ValueError):
    """Raised when required retrieval configuration is missing or invalid."""


@dataclass(frozen=True)
class RetrievalConfig:
    api_key: str
    base_url: str
    model_name: str
    vision_model_name: str
    embedding_base_url: str
    embedding_model_name: str
    embedding_api_key: str
    rerank_base_url: str
    rerank_model_name: str
    rerank_api_key: str
    milvus_mode: str
    milvus_lite_path: Path
    milvus_uri: str
    milvus_token: str
    milvus_collection: str
    milvus_course_collection: str
    milvus_vector_dim: int | None = None

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        env_file: Path | None = Path(".env"),
        *,
        require_chat: bool = True,
    ) -> "RetrievalConfig":
        values = load_env_values(env=env, env_file=env_file)

        required = [
            "EMBEDDING_BASE_URL",
            "EMBEDDING_MODEL_NAME",
            "EMBEDDING_API_KEY",
            "RERANK_BASE_URL",
            "RERANK_MODEL_NAME",
            "RERANK_API_KEY",
        ]
        if require_chat:
            required = ["API_KEY", "BASE_URL", "MODEL_NAME", *required]
        missing = [key for key in required if not values.get(key, "").strip()]
        if missing:
            raise ConfigError(f"缺少必要环境变量: {', '.join(missing)}")

        vector_dim = values.get("MILVUS_VECTOR_DIM", "").strip()
        milvus_mode = values.get("MILVUS_MODE", "lite").strip().lower() or "lite"
        if milvus_mode not in {"lite", "docker"}:
            raise ConfigError("MILVUS_MODE 仅支持 lite 或 docker")
        return cls(
            api_key=values.get("API_KEY", "").strip(),
            base_url=values.get("BASE_URL", "").strip(),
            model_name=values.get("MODEL_NAME", "").strip(),
            vision_model_name=values.get("VISION_MODEL_NAME", "").strip(),
            embedding_base_url=values["EMBEDDING_BASE_URL"].strip(),
            embedding_model_name=values["EMBEDDING_MODEL_NAME"].strip(),
            embedding_api_key=values["EMBEDDING_API_KEY"].strip(),
            rerank_base_url=values["RERANK_BASE_URL"].strip(),
            rerank_model_name=values["RERANK_MODEL_NAME"].strip(),
            rerank_api_key=values["RERANK_API_KEY"].strip(),
            milvus_mode=milvus_mode,
            milvus_lite_path=Path(
                values.get("MILVUS_LITE_PATH", ".local/milvus/xhbx_rag.db").strip()
            ),
            milvus_uri=values.get("MILVUS_URI", "http://localhost:19530").strip()
            or "http://localhost:19530",
            milvus_token=values.get("MILVUS_TOKEN", "").strip(),
            milvus_collection=values.get(
                "MILVUS_COLLECTION",
                "xhbx_sales_chunks",
            ).strip()
            or "xhbx_sales_chunks",
            milvus_course_collection=values.get(
                "MILVUS_COURSE_COLLECTION",
                "xhbx_course_chunks",
            ).strip()
            or "xhbx_course_chunks",
            milvus_vector_dim=int(vector_dim) if vector_dim else None,
        )

    def safe_summary(self) -> str:
        return (
            "RetrievalConfig("
            f"base_url={self.base_url}, "
            f"model_name={self.model_name}, "
            f"vision_model_name={self.vision_model_name}, "
            f"embedding_base_url={self.embedding_base_url}, "
            f"embedding_model_name={self.embedding_model_name}, "
            f"rerank_base_url={self.rerank_base_url}, "
            f"rerank_model_name={self.rerank_model_name}, "
            f"milvus_mode={self.milvus_mode}, "
            f"milvus_lite_path={self.milvus_lite_path}, "
            f"milvus_uri={self.milvus_uri}, "
            f"milvus_collection={self.milvus_collection}, "
            f"milvus_course_collection={self.milvus_course_collection}, "
            f"milvus_vector_dim={self.milvus_vector_dim})"
        )


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _strip_quotes(value.strip())
    return values


def load_env_values(
    *,
    env: Mapping[str, str] | None = None,
    env_file: Path | None = Path(".env"),
) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_file is not None and env_file.exists():
        values.update(_read_env_file(env_file))
    values.update(os.environ if env is None else env)
    return values


def ingestion_limits_from_env(
    env: Mapping[str, str] | None = None,
) -> IngestionLimits:
    values = os.environ if env is None else env
    defaults = IngestionLimits()

    def positive_int(key: str, default: int) -> int:
        raw = values.get(key)
        if raw is None or not raw.strip():
            return default
        try:
            parsed = int(raw.strip())
        except ValueError as exc:
            raise ConfigError(f"{key} 必须是正整数") from exc
        if parsed <= 0:
            raise ConfigError(f"{key} 必须是正整数")
        return parsed

    ratio_key = "WEB_INGEST_MAX_COMPRESSION_RATIO"
    ratio_raw = values.get(ratio_key)
    if ratio_raw is None or not ratio_raw.strip():
        ratio = defaults.max_compression_ratio
    else:
        try:
            ratio = float(ratio_raw.strip())
        except ValueError as exc:
            raise ConfigError(f"{ratio_key} 必须是有限正数") from exc
        if not math.isfinite(ratio) or ratio <= 0:
            raise ConfigError(f"{ratio_key} 必须是有限正数")

    return IngestionLimits(
        max_upload_bytes=positive_int(
            "WEB_INGEST_MAX_UPLOAD_BYTES", defaults.max_upload_bytes
        ),
        max_zip_entries=positive_int(
            "WEB_INGEST_MAX_ZIP_ENTRIES", defaults.max_zip_entries
        ),
        max_extracted_bytes=positive_int(
            "WEB_INGEST_MAX_EXTRACTED_BYTES", defaults.max_extracted_bytes
        ),
        max_entry_bytes=positive_int(
            "WEB_INGEST_MAX_ENTRY_BYTES", defaults.max_entry_bytes
        ),
        max_compression_ratio=ratio,
        max_path_chars=defaults.max_path_chars,
    )


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
