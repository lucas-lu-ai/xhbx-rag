from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


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
    milvus_lite_path: Path
    milvus_collection: str
    milvus_vector_dim: int | None = None

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        env_file: Path | None = Path(".env"),
    ) -> "RetrievalConfig":
        values: dict[str, str] = {}
        if env_file is not None and env_file.exists():
            values.update(_read_env_file(env_file))
        values.update(os.environ if env is None else env)

        required = [
            "API_KEY",
            "BASE_URL",
            "MODEL_NAME",
            "EMBEDDING_BASE_URL",
            "EMBEDDING_MODEL_NAME",
            "EMBEDDING_API_KEY",
            "RERANK_BASE_URL",
            "RERANK_MODEL_NAME",
            "RERANK_API_KEY",
        ]
        missing = [key for key in required if not values.get(key, "").strip()]
        if missing:
            raise ConfigError(f"缺少必要环境变量: {', '.join(missing)}")

        vector_dim = values.get("MILVUS_VECTOR_DIM", "").strip()
        return cls(
            api_key=values["API_KEY"].strip(),
            base_url=values["BASE_URL"].strip(),
            model_name=values["MODEL_NAME"].strip(),
            vision_model_name=values.get("VISION_MODEL_NAME", "").strip(),
            embedding_base_url=values["EMBEDDING_BASE_URL"].strip(),
            embedding_model_name=values["EMBEDDING_MODEL_NAME"].strip(),
            embedding_api_key=values["EMBEDDING_API_KEY"].strip(),
            rerank_base_url=values["RERANK_BASE_URL"].strip(),
            rerank_model_name=values["RERANK_MODEL_NAME"].strip(),
            rerank_api_key=values["RERANK_API_KEY"].strip(),
            milvus_lite_path=Path(
                values.get("MILVUS_LITE_PATH", ".local/milvus/xhbx_rag.db").strip()
            ),
            milvus_collection=values.get(
                "MILVUS_COLLECTION",
                "xhbx_sales_chunks",
            ).strip()
            or "xhbx_sales_chunks",
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
            f"milvus_lite_path={self.milvus_lite_path}, "
            f"milvus_collection={self.milvus_collection}, "
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


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
