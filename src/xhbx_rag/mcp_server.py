"""基于 MCP 协议对外暴露销售知识检索能力。

复用与 CLI/Web 完全相同的检索链（query understanding → 向量 + 关键词
混合召回 → RRF 融合 → rerank），通过 stdio（默认）或 streamable-http
供 MCP 客户端（Claude Code、Claude Desktop 等）调用。

对外错误文案走白名单归一（与 web/safe_errors.py 同构），
未知异常不泄漏内部路径与堆栈。
"""

from __future__ import annotations

import argparse
from threading import Lock
from typing import Any, Callable, Protocol, Sequence

from mcp.server.fastmcp import FastMCP

from .config import RetrievalConfig
from .embedding import EmbeddingClient
from .milvus_store import create_milvus_store
from .query_understanding import QueryUnderstandingAgent
from .rerank import RerankClient
from .resource_utils import close_resources, is_local_index_open_failure
from .search import search_evidence

SERVER_NAME = "xhbx-rag"
SERVER_INSTRUCTIONS = (
    "保险绩优案例销售知识检索服务。"
    "用 search_knowledge 输入自然语言问题，返回经混合召回与重排的证据 chunk"
    "（含知识类型、原文引用与定位）；用 retrieval_status 查看索引与配置状态。"
)

DEFAULT_TOP_N = 20
DEFAULT_TOP_K = 5

# 仅对 streamable-http 传输生效；stdio 模式忽略。
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000

UNAVAILABLE_SEARCH_ERROR = "检索服务暂时不可用"
SAFE_CONFIG_PARSE_ERROR = "配置解析失败，请检查 .env 中的数值配置。"
LOCAL_INDEX_UNAVAILABLE_ERROR = (
    "本地 Milvus 索引暂时不可用，请关闭其他正在使用索引的进程后重试。"
)
_MISSING_CONFIG_ERROR_PREFIX = "缺少必要环境变量:"
_SAFE_ERROR_MESSAGES = frozenset(
    {
        "问题不能为空",
        "top_n 必须在 1 到 100 之间",
        "top_k 必须在 1 到 20 之间",
        "top_k 不能大于 top_n",
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
    def search(self, *, query: str, top_n: int, top_k: int) -> dict:
        """执行完整检索链并返回 search_evidence 结果。"""


class ConfiguredEvidenceSearcher:
    """按调用构建检索资源并在结束后关闭，与 CLI/Web 共用同一条检索链。"""

    def search(self, *, query: str, top_n: int, top_k: int) -> dict:
        config = RetrievalConfig.from_env()
        if config.milvus_mode == "lite":
            with _LITE_SEARCH_LOCK:
                return self._search_with_config(
                    config, query=query, top_n=top_n, top_k=top_k
                )
        return self._search_with_config(config, query=query, top_n=top_n, top_k=top_k)

    def _search_with_config(
        self,
        config: RetrievalConfig,
        *,
        query: str,
        top_n: int,
        top_k: int,
    ) -> dict:
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
            try:
                store = create_milvus_store(config)
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
            return search_evidence(
                query=query,
                query_agent=query_agent,
                embedding_client=embedding_client,
                store=store,
                reranker=reranker,
                top_n=top_n,
                top_k=top_k,
            )
        finally:
            close_resources(resources)


def create_mcp_server(
    searcher: EvidenceSearcher | None = None,
    status_provider: Callable[[], dict[str, Any]] | None = None,
    *,
    host: str = DEFAULT_HTTP_HOST,
    port: int = DEFAULT_HTTP_PORT,
) -> FastMCP:
    active_searcher = searcher if searcher is not None else ConfiguredEvidenceSearcher()
    active_status = (
        status_provider if status_provider is not None else _default_status_provider
    )
    server = FastMCP(SERVER_NAME, instructions=SERVER_INSTRUCTIONS, host=host, port=port)

    @server.tool(
        name="search_knowledge",
        description=(
            "从保险绩优案例知识库检索销售证据。输入自然语言问题，"
            "经问题理解、向量+关键词混合召回、RRF 融合与重排后，"
            "返回 top_k 条证据 chunk（含知识类型、原文引用与定位）。"
            "top_n 为召回候选数（1-100，默认 20），top_k 为最终返回数"
            "（1-20，默认 5），top_k 不能大于 top_n。"
        ),
    )
    def search_knowledge(
        query: str,
        top_n: int = DEFAULT_TOP_N,
        top_k: int = DEFAULT_TOP_K,
    ) -> dict:
        stripped_query = query.strip()
        if not stripped_query:
            raise ValueError("问题不能为空")
        _validate_limits(top_n=top_n, top_k=top_k)
        try:
            return active_searcher.search(
                query=stripped_query, top_n=top_n, top_k=top_k
            )
        except Exception as exc:
            raise ValueError(_safe_error_message(exc)) from exc

    @server.tool(
        name="retrieval_status",
        description=(
            "查看检索服务状态：Milvus 模式与目标、collection 名称、"
            "必要配置是否齐全。不返回任何密钥内容。"
        ),
    )
    def retrieval_status() -> dict:
        try:
            return active_status()
        except Exception as exc:
            raise ValueError(_safe_error_message(exc)) from exc

    return server


def _default_status_provider() -> dict[str, Any]:
    try:
        config = RetrievalConfig.from_env()
    except ValueError as exc:
        return {
            "ok": False,
            "milvus_mode": "",
            "milvus_target": "",
            "milvus_collection": "",
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
        "errors": [],
    }


def _validate_limits(*, top_n: int, top_k: int) -> None:
    if not isinstance(top_n, int) or isinstance(top_n, bool) or not 1 <= top_n <= 100:
        raise ValueError("top_n 必须在 1 到 100 之间")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 20:
        raise ValueError("top_k 必须在 1 到 20 之间")
    if top_k > top_n:
        raise ValueError("top_k 不能大于 top_n")


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
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="传输方式：stdio 供本机客户端（默认），streamable-http 供远程客户端",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HTTP_HOST,
        help=(
            "HTTP 监听地址，仅对 streamable-http 生效，默认 127.0.0.1。"
            "服务无鉴权，绑定非回环地址前请确认处于可信内网"
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_HTTP_PORT,
        help="HTTP 监听端口，仅对 streamable-http 生效，默认 8000",
    )
    args = parser.parse_args(argv)
    create_mcp_server(host=args.host, port=args.port).run(transport=args.transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

    # uv run xhbx-rag-mcp --transport streamable-http --port 9331
    #     name="xhbx_rag",
    #     transport="streamable_http",
    #     url="http://<服务机IP>:9331/mcp"