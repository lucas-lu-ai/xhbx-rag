"""基于 MCP 协议对外暴露销售知识检索能力。

MCP 侧只做检索：原始 query → embedding → 向量 + 关键词混合召回
→ RRF 融合 → rerank。不会调用 chat/completions 做 query understanding。

对外错误文案走白名单归一（与 web/safe_errors.py 同构），
未知异常不泄漏内部路径与堆栈。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping, Protocol, Sequence

from mcp.server.fastmcp import FastMCP

from .config import RetrievalConfig, load_env_values
from .embedding import EmbeddingClient
from .milvus_store import MilvusSearchHit, create_retrieval_store
from .rerank import RerankClient
from .resource_utils import close_resources, is_local_index_open_failure

SERVER_NAME = "xhbx-rag"
KB_SERVER_INSTRUCTIONS = (
    "保险绩优案例销售知识检索服务。"
    "用 kb_list_knowledge_bases 查看可用知识库；用 kb_search_knowledge "
    "输入自然语言问题和知识库 ID，返回统一 McpResponse 包装的知识切片结果。"
)
LEGACY_SERVER_INSTRUCTIONS = (
    "保险绩优案例销售知识检索服务。"
    "用 search_knowledge 输入自然语言问题，返回经 embedding 检索与重排的证据 chunk"
    "（含知识类型、原文引用与定位）；用 retrieval_status 查看索引与配置状态。"
)
BOTH_SERVER_INSTRUCTIONS = (
    "保险绩优案例销售知识检索服务。默认建议使用 kb_list_knowledge_bases 和 "
    "kb_search_knowledge；旧客户端也可继续使用 search_knowledge。"
)
SERVER_INSTRUCTIONS = KB_SERVER_INSTRUCTIONS

DEFAULT_TOP_N = 20
DEFAULT_TOP_K = 5

# 仅对 HTTP 类传输（streamable-http / sse）生效；stdio 模式忽略。
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000
DEFAULT_STREAMABLE_HTTP_PATH = "/mcp"
DEFAULT_SSE_PATH = "/sse"
CHUNK_TYPE_LABELS = {
    "customer_journey": "客户旅程",
    "strategy": "销售策略",
    "script": "场景话术",
    "objection_handling": "异议处理",
    "training_course": "培训课程",
}
KB_CASE_ID = 1
KB_COURSE_ID = 2
VISIBLE_KNOWLEDGE_BASES = [
    {
        "kbId": KB_CASE_ID,
        "name": "保险绩优案例库",
        "description": "保险绩优案例销售知识，包含客户旅程、销售策略、场景话术和异议处理。",
    },
    {
        "kbId": KB_COURSE_ID,
        "name": "培训课程库",
        "description": "保险销售培训课程知识，包含课件、讲师备注和课程切片。",
    },
]
CASE_KB_CHUNK_TYPES = [
    "customer_journey",
    "strategy",
    "script",
    "objection_handling",
]
COURSE_KB_CHUNK_TYPES = ["training_course"]
DEFAULT_KB_TOP_K = 10
MAX_KB_TOP_K = 50
SLICE_PREVIEW_CHARS = 240
DEFAULT_KNOWLEDGE_TYPES = ["QA", "SLICE", "KNOWLEDGE_POINT"]
SUPPORTED_KB_RETRIEVAL_MODE = "HYBRID"
MCP_TOOL_PROFILE_ENV = "MCP_TOOL_PROFILE"
TOOL_PROFILE_KB = "kb"
TOOL_PROFILE_LEGACY = "legacy"
TOOL_PROFILE_BOTH = "both"
SUPPORTED_TOOL_PROFILES = {
    TOOL_PROFILE_KB,
    TOOL_PROFILE_LEGACY,
    TOOL_PROFILE_BOTH,
}

UNAVAILABLE_SEARCH_ERROR = "检索服务暂时不可用"
SAFE_CONFIG_PARSE_ERROR = "配置解析失败，请检查 .env 中的数值配置。"
LOCAL_INDEX_UNAVAILABLE_ERROR = (
    "本地 Milvus 索引暂时不可用，请关闭其他正在使用索引的进程后重试。"
)
_MISSING_CONFIG_ERROR_PREFIX = "缺少必要环境变量:"
_SAFE_ERROR_MESSAGES = frozenset(
    {
        "问题不能为空",
        SAFE_CONFIG_PARSE_ERROR,
        LOCAL_INDEX_UNAVAILABLE_ERROR,
    }
)
_SAFE_CONFIG_KEYS = frozenset(
    {
        "API_KEY",
        "BASE_URL",
        "MODEL_NAME",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_MODEL_NAME",
        "EMBEDDING_API_KEY",
        "RERANK_BASE_URL",
        "RERANK_MODEL_NAME",
        "RERANK_API_KEY",
    }
)

# Milvus Lite 是单进程文件库：进程内所有检索共用一把锁串行执行。
_LITE_SEARCH_LOCK = Lock()


class EvidenceSearcher(Protocol):
    def search(
        self,
        *,
        query: str,
        top_n: int,
        top_k: int,
        filters: dict | None = None,
    ) -> dict:
        """执行完整检索链并返回 search_evidence 结果。"""


class FilterOptionsProvider(Protocol):
    def filter_options(self) -> dict:
        """返回当前索引中可用的过滤条件合法值。"""


class ConfiguredEvidenceSearcher:
    """按调用构建检索资源并在结束后关闭；MCP 不调用 chat 大模型。"""

    def search(
        self,
        *,
        query: str,
        top_n: int,
        top_k: int,
        filters: dict | None = None,
    ) -> dict:
        config = RetrievalConfig.from_env(require_chat=False)
        if config.milvus_mode == "lite":
            with _LITE_SEARCH_LOCK:
                return self._search_with_config(
                    config, query=query, top_n=top_n, top_k=top_k, filters=filters
                )
        return self._search_with_config(
            config, query=query, top_n=top_n, top_k=top_k, filters=filters
        )

    def _search_with_config(
        self,
        config: RetrievalConfig,
        *,
        query: str,
        top_n: int,
        top_k: int,
        filters: dict | None = None,
    ) -> dict:
        resources: list[object] = []
        try:
            embedding_client = EmbeddingClient(
                base_url=config.embedding_base_url,
                api_key=config.embedding_api_key,
                model=config.embedding_model_name,
            )
            resources.append(embedding_client)
            try:
                store = create_retrieval_store(config)
            except Exception as exc:
                if config.milvus_mode == "lite" and is_local_index_open_failure(exc):
                    raise ValueError(LOCAL_INDEX_UNAVAILABLE_ERROR) from exc
                raise
            resources.append(store)
            reranker = RerankClient(
                base_url=config.rerank_base_url,
                api_key=config.rerank_api_key,
                model=config.rerank_model_name,
            )
            resources.append(reranker)
            return _direct_search_evidence(
                query=query,
                embedding_client=embedding_client,
                store=store,
                reranker=reranker,
                top_n=top_n,
                top_k=top_k,
                filters=filters or {},
            )
        finally:
            close_resources(resources)


class ConfiguredFilterOptionsProvider:
    """按调用读取当前索引中的可用过滤值；不调用 chat 大模型。"""

    def filter_options(self) -> dict:
        config = RetrievalConfig.from_env(require_chat=False)
        if config.milvus_mode == "lite":
            with _LITE_SEARCH_LOCK:
                return self._filter_options_with_config(config)
        return self._filter_options_with_config(config)

    def _filter_options_with_config(self, config: RetrievalConfig) -> dict:
        resources: list[object] = []
        try:
            try:
                store = create_retrieval_store(config)
            except Exception as exc:
                if config.milvus_mode == "lite" and is_local_index_open_failure(exc):
                    raise ValueError(LOCAL_INDEX_UNAVAILABLE_ERROR) from exc
                raise
            resources.append(store)
            return _format_filter_options(store.filter_options())
        finally:
            close_resources(resources)


def create_mcp_server(
    searcher: EvidenceSearcher | None = None,
    status_provider: Callable[[], dict[str, Any]] | None = None,
    filter_options_provider: FilterOptionsProvider | None = None,
    *,
    host: str = DEFAULT_HTTP_HOST,
    port: int = DEFAULT_HTTP_PORT,
    sse_path: str = DEFAULT_SSE_PATH,
    streamable_http_path: str = DEFAULT_STREAMABLE_HTTP_PATH,
    expose_legacy_tools: bool = False,
    tool_profile: str = TOOL_PROFILE_KB,
) -> FastMCP:
    active_tool_profile = _normalize_tool_profile(tool_profile)
    if expose_legacy_tools:
        active_tool_profile = TOOL_PROFILE_BOTH
    active_searcher = searcher if searcher is not None else ConfiguredEvidenceSearcher()
    active_status = (
        status_provider if status_provider is not None else _default_status_provider
    )
    active_filter_options_provider = (
        filter_options_provider
        if filter_options_provider is not None
        else ConfiguredFilterOptionsProvider()
    )
    server = FastMCP(
        SERVER_NAME,
        instructions=_server_instructions(active_tool_profile),
        host=host,
        port=port,
        sse_path=sse_path,
        streamable_http_path=streamable_http_path,
    )

    def kb_list_knowledge_bases() -> dict:
        return _mcp_success([dict(item) for item in VISIBLE_KNOWLEDGE_BASES])

    def kb_search_knowledge(
        query: str,
        kbId: int,
        knowledgeTypes: list[str] | None = None,
        retrievalMode: str = SUPPORTED_KB_RETRIEVAL_MODE,
        hybridWeights: dict[str, Any] | None = None,
        topK: int = DEFAULT_KB_TOP_K,
        includeDetails: bool = False,
    ) -> dict:
        stripped_query = str(query or "").strip()
        if not stripped_query:
            return _mcp_error("10004", "参数错误: query 不能为空")

        filters = _kb_filters(kbId)
        if filters is None:
            return _mcp_error("10003", "当前用户对指定知识库无访问权限")

        try:
            top_k = _normalize_kb_top_k(topK)
            retrieval_mode = str(retrievalMode or "").strip().upper()
            if retrieval_mode != SUPPORTED_KB_RETRIEVAL_MODE:
                return _mcp_error(
                    "10004",
                    "参数错误: retrievalMode 暂时仅支持 HYBRID",
                )
            if hybridWeights is not None and not isinstance(hybridWeights, dict):
                return _mcp_error("10004", "参数错误: hybridWeights 必须为对象")
            knowledge_types = _normalize_knowledge_types(knowledgeTypes)
        except (TypeError, ValueError) as exc:
            return _mcp_error("10004", str(exc))

        if "SLICE" not in knowledge_types:
            return _mcp_success([])

        try:
            result = active_searcher.search(
                query=stripped_query,
                top_n=max(DEFAULT_TOP_N, top_k),
                top_k=top_k,
                filters=filters,
            )
        except Exception as exc:
            return _mcp_error("500", _safe_error_message(exc))
        formatted = (
            _format_kb_search_results(result)
            if includeDetails
            else _format_compact_kb_search_results(result)
        )
        return _mcp_success(formatted)

    def search_knowledge(
        query: str,
        chunk_types: list[str] | None = None,
        stage: str = "",
        case_name: str = "",
    ) -> dict:
        stripped_query = query.strip()
        if not stripped_query:
            raise ValueError("问题不能为空")
        filters = _build_optional_filters(
            chunk_types=chunk_types,
            stage=stage,
            case_name=case_name,
        )
        try:
            return active_searcher.search(
                query=stripped_query,
                top_n=DEFAULT_TOP_N,
                top_k=DEFAULT_TOP_K,
                filters=filters,
            )
        except Exception as exc:
            raise ValueError(_safe_error_message(exc)) from exc

    def retrieval_status() -> dict:
        try:
            return active_status()
        except Exception as exc:
            raise ValueError(_safe_error_message(exc)) from exc

    def list_filter_options() -> dict:
        try:
            return active_filter_options_provider.filter_options()
        except Exception as exc:
            raise ValueError(_safe_error_message(exc)) from exc

    if active_tool_profile in {TOOL_PROFILE_KB, TOOL_PROFILE_BOTH}:
        server.tool(
            name="kb_list_knowledge_bases",
            description="列出当前用户有权限查阅的知识库。",
        )(kb_list_knowledge_bases)
        server.tool(
            name="kb_search_knowledge",
            description=(
                "在指定知识库中统一检索 QA、文档切片、知识点。调用前应先使用 "
                "kb_list_knowledge_bases 获取可见知识库 ID。"
            ),
        )(kb_search_knowledge)

    if active_tool_profile in {TOOL_PROFILE_LEGACY, TOOL_PROFILE_BOTH}:
        server.tool(
            name="search_knowledge",
            description=(
                "从保险绩优案例知识库检索销售证据。输入自然语言问题，"
                "经 embedding、向量+关键词混合召回、RRF 融合与重排后，"
                "返回证据 chunk（含知识类型、原文引用与定位）。"
                "可选传入 chunk_types、stage、case_name 做精确过滤；"
            ),
        )(search_knowledge)
        server.tool(
            name="retrieval_status",
            description=(
                "查看检索服务状态：Milvus 模式与目标、collection 名称、"
                "必要配置是否齐全。不返回任何密钥内容。"
            ),
        )(retrieval_status)
        server.tool(
            name="list_filter_options",
            description=(
                "列出 search_knowledge 可用的精确过滤值：知识类型、销售阶段、案例名称。"
                "客户端应先读取这些合法值，再决定是否传入过滤参数。"
            ),
        )(list_filter_options)

    return server


def _tool_profile_from_env(
    *,
    env: Mapping[str, str] | None = None,
    env_file: Path | None = Path(".env"),
) -> str:
    values = load_env_values(env=env, env_file=env_file)
    return _normalize_tool_profile(values.get(MCP_TOOL_PROFILE_ENV, TOOL_PROFILE_KB))


def _normalize_tool_profile(value: str | None) -> str:
    profile = str(value or TOOL_PROFILE_KB).strip().lower() or TOOL_PROFILE_KB
    if profile not in SUPPORTED_TOOL_PROFILES:
        raise ValueError("MCP_TOOL_PROFILE 仅支持 kb、legacy 或 both")
    return profile


def _server_instructions(tool_profile: str) -> str:
    if tool_profile == TOOL_PROFILE_LEGACY:
        return LEGACY_SERVER_INSTRUCTIONS
    if tool_profile == TOOL_PROFILE_BOTH:
        return BOTH_SERVER_INSTRUCTIONS
    return KB_SERVER_INSTRUCTIONS


def _mcp_success(data: Any) -> dict[str, Any]:
    return {
        "success": True,
        "data": data,
        "errorCode": None,
        "errorMessage": None,
    }


def _mcp_error(error_code: str, error_message: str) -> dict[str, Any]:
    return {
        "success": False,
        "data": None,
        "errorCode": error_code,
        "errorMessage": error_message,
    }


def _kb_filters(kb_id: int) -> dict[str, Any] | None:
    if kb_id == KB_CASE_ID:
        return {"chunk_types": CASE_KB_CHUNK_TYPES}
    if kb_id == KB_COURSE_ID:
        return {"chunk_types": COURSE_KB_CHUNK_TYPES}
    return None


def _normalize_kb_top_k(top_k: int | None) -> int:
    try:
        value = DEFAULT_KB_TOP_K if top_k is None else int(top_k)
    except (TypeError, ValueError) as exc:
        raise ValueError("参数错误: topK 必须在 1 到 50 之间") from exc
    if value < 1 or value > MAX_KB_TOP_K:
        raise ValueError("参数错误: topK 必须在 1 到 50 之间")
    return value


def _normalize_knowledge_types(knowledge_types: list[str] | None) -> list[str]:
    if knowledge_types is None:
        return list(DEFAULT_KNOWLEDGE_TYPES)
    return [
        str(knowledge_type).strip().upper()
        for knowledge_type in knowledge_types
        if str(knowledge_type).strip()
    ]


def _format_compact_kb_search_results(
    result: dict[str, Any],
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    raw_results = result.get("results", [])
    if not isinstance(raw_results, list):
        return items
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        citations = raw.get("citations")
        first_citation = (
            citations[0]
            if isinstance(citations, list)
            and citations
            and isinstance(citations[0], dict)
            else {}
        )
        items.append(
            {
                "content": str(raw.get("text") or ""),
                "source_path": str(first_citation.get("source_path") or ""),
                "filename": str(first_citation.get("filename") or ""),
            }
        )
    return items


def _format_kb_search_results(result: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    raw_results = result.get("results", [])
    if not isinstance(raw_results, list):
        return items
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        full_content = str(raw.get("text") or "")
        content, content_truncated = _preview_slice_content(full_content)
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        citations = raw.get("citations") if isinstance(raw.get("citations"), list) else []
        items.append(
            {
                "id": raw.get("chunk_id"),
                "knowledgeType": "SLICE",
                "score": raw.get("rerank_score", raw.get("score")),
                "tags": metadata.get("tag_paths") or None,
                "qa": None,
                "slice": {
                    "content": content,
                    "fullContent": full_content,
                    "contentTruncated": content_truncated,
                    "sliceType": raw.get("chunk_type"),
                    "parentId": metadata.get("parent_id"),
                    "titlePath": metadata.get("title_path"),
                    "parentSliceContext": metadata.get("parent_slice_context"),
                    "citations": citations,
                },
                "knowledgePoint": None,
            }
        )
    return items


def _preview_slice_content(content: str) -> tuple[str, bool]:
    if len(content) <= SLICE_PREVIEW_CHARS:
        return content, False
    return content[:SLICE_PREVIEW_CHARS].rstrip() + "...", True


def _direct_search_evidence(
    *,
    query: str,
    embedding_client: Any,
    store: Any,
    reranker: Any,
    top_n: int,
    top_k: int,
    filters: dict,
) -> dict:
    vector = embedding_client.embed_query(query)
    vector_hits = store.search(vector=vector, top_k=top_n, filters=filters)
    keyword_hits = _keyword_search_if_available(
        store,
        query=query,
        top_k=top_n,
        filters=filters,
    )
    candidates = (
        _rrf_fuse(vector_hits, keyword_hits, limit=top_n)
        if keyword_hits is not None
        else vector_hits[:top_n]
    )
    reranked = reranker.rerank(
        query,
        [hit.chunk.text for hit in candidates],
        top_k=top_k,
    )
    return {
        "original_query": query,
        "rewritten_query": query,
        "intent": "direct_retrieval",
        "filters": filters,
        "results": [
            _serialize_hit(candidates[item.index], item.relevance_score)
            for item in reranked
        ],
    }


def _build_optional_filters(
    *,
    chunk_types: list[str] | None,
    stage: str,
    case_name: str,
) -> dict:
    filters: dict[str, Any] = {}
    normalized_chunk_types = [
        str(chunk_type).strip()
        for chunk_type in chunk_types or []
        if str(chunk_type).strip()
    ]
    if normalized_chunk_types:
        filters["chunk_types"] = normalized_chunk_types
    stripped_stage = stage.strip()
    if stripped_stage:
        filters["stage"] = stripped_stage
    stripped_case_name = case_name.strip()
    if stripped_case_name:
        filters["case_name"] = stripped_case_name
    return filters


def _format_filter_options(options: dict[str, Any]) -> dict:
    chunk_type_values = [
        str(value).strip()
        for value in options.get("chunk_types", [])
        if str(value).strip()
    ]
    return {
        "chunk_types": [
            {"value": value, "label": CHUNK_TYPE_LABELS.get(value, value)}
            for value in chunk_type_values
        ],
        "stages": _str_values(options.get("stages", [])),
        "case_names": _str_values(options.get("case_names", [])),
    }


def _str_values(values: Any) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()]


def _keyword_search_if_available(
    store: Any,
    *,
    query: str,
    top_k: int,
    filters: dict,
) -> list[MilvusSearchHit] | None:
    keyword_search = getattr(store, "keyword_search", None)
    if keyword_search is None:
        return None
    return keyword_search(query=query, top_k=top_k, filters=filters)


def _rrf_fuse(
    vector_hits: list[MilvusSearchHit],
    keyword_hits: list[MilvusSearchHit],
    *,
    limit: int,
) -> list[MilvusSearchHit]:
    scores: dict[str, float] = {}
    hits_by_id: dict[str, MilvusSearchHit] = {}
    first_seen: dict[str, int] = {}
    seen_order = 0
    rrf_k = 60

    for hit_list in (vector_hits, keyword_hits):
        for rank, hit in enumerate(hit_list, start=1):
            chunk_id = hit.chunk.chunk_id
            if chunk_id not in hits_by_id:
                hits_by_id[chunk_id] = hit
                first_seen[chunk_id] = seen_order
                seen_order += 1
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (rrf_k + rank)

    ranked_ids = sorted(
        scores,
        key=lambda chunk_id: (-scores[chunk_id], first_seen[chunk_id]),
    )
    return [
        MilvusSearchHit(chunk=hits_by_id[chunk_id].chunk, score=scores[chunk_id])
        for chunk_id in ranked_ids[:limit]
    ]


def _serialize_hit(hit: MilvusSearchHit, rerank_score: float) -> dict:
    return {
        "chunk_id": hit.chunk.chunk_id,
        "chunk_type": hit.chunk.chunk_type,
        "text": hit.chunk.text,
        "score": hit.score,
        "rerank_score": rerank_score,
        "matched_tag_paths": [],
        "tag_boost_factor": 1.0,
        "metadata": hit.chunk.metadata,
        "citations": [
            citation.model_dump(mode="json") for citation in hit.chunk.citations
        ],
    }


def _default_status_provider() -> dict[str, Any]:
    try:
        config = RetrievalConfig.from_env(require_chat=False)
    except ValueError as exc:
        return {
            "ok": False,
            "milvus_mode": "",
            "milvus_target": "",
            "milvus_collection": "",
            "milvus_course_collection": "",
            "errors": [_safe_error_message(exc)],
        }
    target = (
        config.milvus_uri
        if config.milvus_mode == "docker"
        else str(config.milvus_lite_path)
    )
    return {
        "ok": True,
        "milvus_mode": config.milvus_mode,
        "milvus_target": target,
        "milvus_collection": config.milvus_collection,
        "milvus_course_collection": config.milvus_course_collection,
        "errors": [],
    }


def _safe_error_message(exc: Exception) -> str:
    """把检索异常归一为安全中文文案；未知异常一律返回兜底文案。"""
    if not isinstance(exc, ValueError):
        return UNAVAILABLE_SEARCH_ERROR
    message = str(exc)
    if message in _SAFE_ERROR_MESSAGES:
        return message
    if message.startswith(_MISSING_CONFIG_ERROR_PREFIX):
        # 防篡改：逐个校验缺失键名，避免异常消息夹带内部信息被透传。
        raw_keys = message.removeprefix(_MISSING_CONFIG_ERROR_PREFIX)
        keys = [item.strip() for item in raw_keys.split(",")]
        if keys and all(key in _SAFE_CONFIG_KEYS for key in keys):
            return message
    return UNAVAILABLE_SEARCH_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="xhbx-rag-mcp",
        description="启动销售知识检索 MCP 服务",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help=(
            "传输方式：stdio 供本机客户端（默认），streamable-http 供远程客户端，"
            "sse 兼容只支持旧版 HTTP+SSE 协议的客户端"
        ),
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HTTP_HOST,
        help=(
            "HTTP 监听地址，仅对 streamable-http/sse 生效，默认 127.0.0.1。"
            "服务无鉴权，绑定非回环地址前请确认处于可信内网"
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_HTTP_PORT,
        help="HTTP 监听端口，仅对 streamable-http/sse 生效，默认 8000",
    )
    parser.add_argument(
        "--path",
        default=None,
        help=(
            "HTTP 端点路径，用于适配客户端固定拼接的路径："
            "streamable-http 默认 /mcp，sse 默认 /sse（如客户端拼 /mcp/sse 则传 --path /mcp/sse）"
        ),
    )
    args = parser.parse_args(argv)
    sse_path = (
        args.path
        if args.transport == "sse" and args.path
        else DEFAULT_SSE_PATH
    )
    streamable_http_path = (
        args.path
        if args.transport == "streamable-http" and args.path
        else DEFAULT_STREAMABLE_HTTP_PATH
    )
    try:
        tool_profile = _tool_profile_from_env()
    except ValueError as exc:
        parser.error(str(exc))
    create_mcp_server(
        host=args.host,
        port=args.port,
        sse_path=sse_path,
        streamable_http_path=streamable_http_path,
        tool_profile=tool_profile,
    ).run(transport=args.transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

    # uv run xhbx-rag-mcp --transport streamable-http --port 9331
    #     name="xhbx_rag",
    #     transport="streamable_http",
    #     url="http://<服务机IP>:9331/mcp"

    # uv run xhbx-rag-mcp --transport sse --path /mcp/sse --host 0.0.0.0 --port 9331
    #     name="xhbx_rag",
    #     transport="sse",
    #     url="http://<服务机IP>:9331/mcp/sse",
