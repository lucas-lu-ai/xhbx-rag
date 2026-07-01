from __future__ import annotations

from pathlib import Path
from typing import Any

from xhbx_rag.answer import AnswerAgent, answer_query
from xhbx_rag.config import ConfigError, RetrievalConfig
from xhbx_rag.embedding import EmbeddingClient
from xhbx_rag.milvus_store import MilvusLiteStore
from xhbx_rag.query_understanding import QueryUnderstandingAgent
from xhbx_rag.rerank import RerankClient

from .source_paths import (
    can_reveal_source,
    citation_display_excerpt,
    display_location,
    project_root_from_module,
)


REQUIRED_CONFIG_KEYS = [
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


def get_status(*, project_root: Path | None = None) -> dict[str, Any]:
    root = project_root or project_root_from_module()
    try:
        config = RetrievalConfig.from_env()
    except ConfigError as exc:
        return {
            "ok": False,
            "data_dir": str(root / "data"),
            "milvus_lite_path": "",
            "milvus_collection": "",
            "config": _missing_config_map(str(exc)),
            "errors": [str(exc)],
        }

    return {
        "ok": True,
        "data_dir": str(root / "data"),
        "milvus_lite_path": str(config.milvus_lite_path),
        "milvus_collection": config.milvus_collection,
        "config": {key: True for key in REQUIRED_CONFIG_KEYS},
        "errors": [],
    }


def answer_question(
    *,
    query: str,
    top_n: int,
    top_k: int,
    project_root: Path | None = None,
) -> dict[str, Any]:
    stripped_query = query.strip()
    if not stripped_query:
        raise ValueError("问题不能为空")

    config = RetrievalConfig.from_env()
    result = answer_query(
        query=stripped_query,
        query_agent=QueryUnderstandingAgent(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model_name,
        ),
        embedding_client=EmbeddingClient(
            base_url=config.embedding_base_url,
            api_key=config.embedding_api_key,
            model=config.embedding_model_name,
        ),
        store=MilvusLiteStore(
            db_path=config.milvus_lite_path,
            collection_name=config.milvus_collection,
        ),
        reranker=RerankClient(
            base_url=config.rerank_base_url,
            api_key=config.rerank_api_key,
            model=config.rerank_model_name,
        ),
        answer_agent=AnswerAgent(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model_name,
        ),
        top_n=top_n,
        top_k=top_k,
    )

    normalized = dict(result)
    normalized["citations"] = [
        _citation_for_ui(citation, project_root=project_root)
        for citation in result.get("citations", []) or []
    ]
    return normalized


def _citation_for_ui(
    citation: dict[str, Any],
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    ui_citation = dict(citation)
    locator = citation.get("locator") or {}
    source_path = str(citation.get("source_path") or "")
    ui_citation["display_location"] = display_location(locator)
    ui_citation["display_excerpt"] = citation_display_excerpt(citation)
    ui_citation["can_reveal"] = (
        bool(source_path) and can_reveal_source(source_path, project_root=project_root)
    )
    return ui_citation


def _missing_config_map(error: str) -> dict[str, bool]:
    config = {key: True for key in REQUIRED_CONFIG_KEYS}
    prefix = "缺少必要环境变量:"
    if prefix in error:
        missing = error.split(prefix, 1)[1]
        for key in [item.strip() for item in missing.split(",")]:
            if key in config:
                config[key] = False
    return config
