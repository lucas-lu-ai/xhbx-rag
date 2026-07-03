"""检索链资源的通用回收与故障判定工具。

Web 与 MCP 两个服务面共用：按调用构建的 agent/embedding/store/reranker
统一在请求结束后关闭底层 httpx/Milvus 客户端。
"""

from __future__ import annotations

from typing import Iterator


def close_resources(resources: list[object]) -> None:
    """尽力关闭资源持有的 http 客户端 / Milvus 客户端，忽略关闭失败。"""
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


def is_local_index_open_failure(exc: Exception) -> bool:
    """判断异常链是否为 Milvus Lite 本地文件被其他进程占用导致的打开失败。"""
    return any(
        "Open local milvus failed" in str(item)
        for item in exception_chain(exc)
    )


def exception_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__
