from pathlib import Path

import pytest

from xhbx_rag.config import (
    ConfigError,
    RetrievalConfig,
    WebRetrievalLimits,
    ingestion_limits_from_env,
    web_retrieval_limits_from_env,
)


def _required_env(**overrides: str) -> dict[str, str]:
    values = {
        "API_KEY": "chat",
        "BASE_URL": "https://api.example.com/v1",
        "MODEL_NAME": "chat-model",
        "EMBEDDING_BASE_URL": "https://api.siliconflow.com/v1",
        "EMBEDDING_MODEL_NAME": "embed",
        "EMBEDDING_API_KEY": "embedding-key",
        "RERANK_BASE_URL": "https://api.siliconflow.com/v1",
        "RERANK_MODEL_NAME": "rerank",
        "RERANK_API_KEY": "rerank-key",
    }
    values.update(overrides)
    return values


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
    assert config.milvus_collection == "xhbx_knowledge_chunks"
    assert config.milvus_vector_dim is None
    assert config.vision_model_name == ""
    assert "secret" not in config.safe_summary()


def test_retrieval_config_defaults_to_milvus_lite_mode() -> None:
    config = RetrievalConfig.from_env(env=_required_env(), env_file=None)

    assert config.milvus_mode == "lite"
    assert config.milvus_lite_path == Path(".local/milvus/xhbx_rag.db")
    assert config.milvus_uri == "http://localhost:19530"
    assert config.milvus_token == ""


def test_retrieval_config_reads_docker_milvus_settings_without_exposing_token() -> None:
    config = RetrievalConfig.from_env(
        env=_required_env(
            MILVUS_MODE="docker",
            MILVUS_URI="http://127.0.0.1:19530",
            MILVUS_TOKEN="root:Milvus",
        ),
        env_file=None,
    )

    assert config.milvus_mode == "docker"
    assert config.milvus_uri == "http://127.0.0.1:19530"
    assert config.milvus_token == "root:Milvus"
    assert "root:Milvus" not in config.safe_summary()


def test_retrieval_config_defaults_course_collection() -> None:
    config = RetrievalConfig.from_env(env=_required_env(), env_file=None)

    assert config.milvus_course_collection == "xhbx_course_chunks"


def test_retrieval_config_reads_course_collection_override() -> None:
    config = RetrievalConfig.from_env(
        env=_required_env(MILVUS_COURSE_COLLECTION="my_course_chunks"),
        env_file=None,
    )

    assert config.milvus_course_collection == "my_course_chunks"
    assert "my_course_chunks" in config.safe_summary()


def test_retrieval_config_rejects_unknown_milvus_mode() -> None:
    with pytest.raises(ConfigError, match="MILVUS_MODE"):
        RetrievalConfig.from_env(
            env=_required_env(MILVUS_MODE="remote"),
            env_file=None,
        )


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


def test_retrieval_config_can_skip_chat_keys_for_mcp_retrieval() -> None:
    config = RetrievalConfig.from_env(
        env={
            "EMBEDDING_BASE_URL": "https://api.siliconflow.com/v1",
            "EMBEDDING_MODEL_NAME": "embed",
            "EMBEDDING_API_KEY": "embedding-key",
            "RERANK_BASE_URL": "https://api.siliconflow.com/v1",
            "RERANK_MODEL_NAME": "rerank",
            "RERANK_API_KEY": "rerank-key",
        },
        env_file=None,
        require_chat=False,
    )

    assert config.api_key == ""
    assert config.base_url == ""
    assert config.model_name == ""
    assert config.embedding_model_name == "embed"
    assert config.rerank_model_name == "rerank"


def test_web_retrieval_limits_use_defaults() -> None:
    assert web_retrieval_limits_from_env(env={}, env_file=None) == WebRetrievalLimits(
        top_n=20,
        top_k=5,
    )


def test_web_retrieval_limits_read_custom_values() -> None:
    assert web_retrieval_limits_from_env(
        env={"WEB_RETRIEVAL_TOP_N": "30", "WEB_RETRIEVAL_TOP_K": "8"},
        env_file=None,
    ) == WebRetrievalLimits(top_n=30, top_k=8)


@pytest.mark.parametrize(
    ("env", "message"),
    [
        (
            {"WEB_RETRIEVAL_TOP_N": "abc"},
            "WEB_RETRIEVAL_TOP_N 必须是 1 到 100 之间的整数",
        ),
        (
            {"WEB_RETRIEVAL_TOP_N": "0"},
            "WEB_RETRIEVAL_TOP_N 必须是 1 到 100 之间的整数",
        ),
        (
            {"WEB_RETRIEVAL_TOP_N": "101"},
            "WEB_RETRIEVAL_TOP_N 必须是 1 到 100 之间的整数",
        ),
        (
            {"WEB_RETRIEVAL_TOP_K": "0"},
            "WEB_RETRIEVAL_TOP_K 必须是 1 到 20 之间的整数",
        ),
        (
            {"WEB_RETRIEVAL_TOP_K": "21"},
            "WEB_RETRIEVAL_TOP_K 必须是 1 到 20 之间的整数",
        ),
        (
            {"WEB_RETRIEVAL_TOP_N": "4", "WEB_RETRIEVAL_TOP_K": "5"},
            "WEB_RETRIEVAL_TOP_K 不能大于 WEB_RETRIEVAL_TOP_N",
        ),
    ],
)
def test_web_retrieval_limits_reject_invalid_values(
    env: dict[str, str], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        web_retrieval_limits_from_env(env=env, env_file=None)


def test_ingestion_limits_use_defaults_and_read_overrides() -> None:
    defaults = ingestion_limits_from_env({})

    assert defaults.max_upload_bytes == 536_870_912
    assert defaults.max_zip_entries == 2_000
    assert defaults.max_extracted_bytes == 2_147_483_648
    assert defaults.max_entry_bytes == 536_870_912
    assert defaults.max_compression_ratio == 100.0

    custom = ingestion_limits_from_env(
        {
            "WEB_INGEST_MAX_UPLOAD_BYTES": "1024",
            "WEB_INGEST_MAX_ZIP_ENTRIES": "12",
            "WEB_INGEST_MAX_EXTRACTED_BYTES": "4096",
            "WEB_INGEST_MAX_ENTRY_BYTES": "2048",
            "WEB_INGEST_MAX_COMPRESSION_RATIO": "25.5",
        }
    )
    assert custom.max_upload_bytes == 1024
    assert custom.max_zip_entries == 12
    assert custom.max_extracted_bytes == 4096
    assert custom.max_entry_bytes == 2048
    assert custom.max_compression_ratio == 25.5


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("WEB_INGEST_MAX_UPLOAD_BYTES", "0"),
        ("WEB_INGEST_MAX_ZIP_ENTRIES", "-1"),
        ("WEB_INGEST_MAX_EXTRACTED_BYTES", "1.5"),
        ("WEB_INGEST_MAX_ENTRY_BYTES", "not-an-int"),
        ("WEB_INGEST_MAX_COMPRESSION_RATIO", "0"),
        ("WEB_INGEST_MAX_COMPRESSION_RATIO", "nan"),
        ("WEB_INGEST_MAX_COMPRESSION_RATIO", "inf"),
    ],
)
def test_ingestion_limits_reject_invalid_values(key: str, value: str) -> None:
    with pytest.raises(ConfigError, match=key):
        ingestion_limits_from_env({key: value})
