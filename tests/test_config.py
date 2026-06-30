from pathlib import Path

import pytest

from xhbx_rag.config import ConfigError, RetrievalConfig


def test_retrieval_config_reads_env_file_without_exposing_secrets(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "API_KEY=chat-secret",
                "BASE_URL=https://api.example.com/v1",
                "MODEL_NAME=chat-model",
                "EMBEDDING_BASE_URL=https://api.siliconflow.com/v1",
                "EMBEDDING_MODEL_NAME=Qwen/Qwen3-Embedding-8B",
                "EMBEDDING_API_KEY=embedding-secret",
                "RERANK_BASE_URL=https://api.siliconflow.com/v1",
                "RERANK_MODEL_NAME=Qwen/Qwen3-Reranker-8B",
                "RERANK_API_KEY=rerank-secret",
            ]
        ),
        encoding="utf-8",
    )

    config = RetrievalConfig.from_env(env={}, env_file=env_file)

    assert config.embedding_api_key == "embedding-secret"
    assert config.rerank_api_key == "rerank-secret"
    assert config.milvus_lite_path == Path(".local/milvus/xhbx_rag.db")
    assert config.milvus_collection == "xhbx_sales_chunks"
    assert config.milvus_vector_dim is None
    assert config.vision_model_name == ""
    assert "secret" not in config.safe_summary()


def test_retrieval_config_reads_optional_vision_model_name() -> None:
    config = RetrievalConfig.from_env(
        env={
            "API_KEY": "chat",
            "BASE_URL": "https://api.example.com/v1",
            "MODEL_NAME": "chat-model",
            "VISION_MODEL_NAME": "qwen3.7-plus",
            "EMBEDDING_BASE_URL": "https://api.siliconflow.com/v1",
            "EMBEDDING_MODEL_NAME": "embed",
            "EMBEDDING_API_KEY": "embedding-key",
            "RERANK_BASE_URL": "https://api.siliconflow.com/v1",
            "RERANK_MODEL_NAME": "rerank",
            "RERANK_API_KEY": "rerank-key",
        },
        env_file=None,
    )

    assert config.vision_model_name == "qwen3.7-plus"


def test_retrieval_config_requires_embedding_and_rerank_keys() -> None:
    with pytest.raises(ConfigError, match="EMBEDDING_API_KEY"):
        RetrievalConfig.from_env(
            env={
                "API_KEY": "chat",
                "BASE_URL": "https://api.example.com/v1",
                "MODEL_NAME": "chat-model",
                "EMBEDDING_BASE_URL": "https://api.siliconflow.com/v1",
                "EMBEDDING_MODEL_NAME": "embed",
                "RERANK_BASE_URL": "https://api.siliconflow.com/v1",
                "RERANK_MODEL_NAME": "rerank",
                "RERANK_API_KEY": "rerank-key",
            },
            env_file=None,
        )
