from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

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
SAFE_CONFIG_PARSE_ERROR = "配置解析失败，请检查 .env 中的数值配置。"


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
    except ValueError:
        return {
            "ok": False,
            "data_dir": str(root / "data"),
            "milvus_lite_path": "",
            "milvus_collection": "",
            "config": _missing_config_map(SAFE_CONFIG_PARSE_ERROR),
            "errors": [SAFE_CONFIG_PARSE_ERROR],
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
    _validate_limits(top_n=top_n, top_k=top_k)

    try:
        config = RetrievalConfig.from_env()
    except ConfigError as exc:
        raise ValueError(str(exc)) from exc
    except ValueError as exc:
        raise ValueError(SAFE_CONFIG_PARSE_ERROR) from exc

    resources: list[object] = []
    try:
        query_agent = QueryUnderstandingAgent(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model_name,
        )
        resources.append(query_agent)
        embedding_client = EmbeddingClient(
            base_url=config.embedding_base_url,
            api_key=config.embedding_api_key,
            model=config.embedding_model_name,
        )
        resources.append(embedding_client)
        store = MilvusLiteStore(
            db_path=config.milvus_lite_path,
            collection_name=config.milvus_collection,
        )
        resources.append(store)
        reranker = RerankClient(
            base_url=config.rerank_base_url,
            api_key=config.rerank_api_key,
            model=config.rerank_model_name,
        )
        resources.append(reranker)
        answer_agent = AnswerAgent(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model_name,
        )
        resources.append(answer_agent)
        result = answer_query(
            query=stripped_query,
            query_agent=query_agent,
            embedding_client=embedding_client,
            store=store,
            reranker=reranker,
            answer_agent=answer_agent,
            top_n=top_n,
            top_k=top_k,
        )
    finally:
        _close_resources(resources)

    normalized = dict(result)
    normalized["citations"] = [
        _citation_for_ui(citation, project_root=project_root)
        for citation in result.get("citations", []) or []
    ]
    return normalized


def _citation_for_ui(
    citation: object,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(citation, Mapping):
        return {
            "display_location": "未提供精确位置",
            "display_excerpt": str(citation or ""),
            "can_reveal": False,
        }

    ui_citation = dict(citation)
    locator = citation.get("locator") or {}
    source_path = str(citation.get("source_path") or "")
    ui_citation["display_location"] = display_location(locator)
    ui_citation["display_excerpt"] = citation_display_excerpt(citation)
    ui_citation["can_reveal"] = (
        bool(source_path) and can_reveal_source(source_path, project_root=project_root)
    )
    return ui_citation


def _close_resources(resources: list[object]) -> None:
    closed: set[int] = set()
    for resource in resources:
        for target in (
            getattr(resource, "http_client", None),
            getattr(resource, "client", None),
            resource,
        ):
            if target is None or id(target) in closed:
                continue
            close = getattr(target, "close", None)
            if not callable(close):
                continue
            closed.add(id(target))
            try:
                close()
            except Exception:
                pass


def _validate_limits(*, top_n: int, top_k: int) -> None:
    if not isinstance(top_n, int) or isinstance(top_n, bool) or not 1 <= top_n <= 100:
        raise ValueError("top_n 必须在 1 到 100 之间")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 20:
        raise ValueError("top_k 必须在 1 到 20 之间")
    if top_k > top_n:
        raise ValueError("top_k 不能大于 top_n")


def _missing_config_map(error: str) -> dict[str, bool]:
    config = {key: True for key in REQUIRED_CONFIG_KEYS}
    prefix = "缺少必要环境变量:"
    if prefix in error:
        missing = error.split(prefix, 1)[1]
        for key in [item.strip() for item in missing.split(",")]:
            if key in config:
                config[key] = False
    return config
