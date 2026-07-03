"""批量执行器：单 worker 线程跨 run 串行，run 内可选并发执行行。

- worker 为 daemon 线程 + queue.Queue + None 停止哨兵，保证进程退出不被阻塞。
- execute_run 为公开同步方法，测试可直接调用而不必启动线程。
- 行级异常只影响单行（error 先安全归一后落库）；run 级异常把 run 标记为 interrupted。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Protocol

from xhbx_rag.config import RetrievalConfig

from .batch_store import BatchRunStore
from .safe_errors import answer_exception_detail
from .services import answer_question, batch_concurrency

logger = logging.getLogger(__name__)

_WORKER_JOIN_TIMEOUT_SECONDS = 10

AnswerFn = Callable[[str, int, int], dict[str, Any]]
ConcurrencyProvider = Callable[[], int]


class BatchStoreProtocol(Protocol):
    """执行器依赖的存储接口（生产实现为 BatchRunStore）。"""

    def claim_run(self, run_id: str) -> bool: ...

    def fetch_pending_row_indexes(self, run_id: str) -> list[int]: ...

    def mark_row_running(self, run_id: str, row_index: int) -> bool: ...

    def get_question(self, run_id: str, row_index: int) -> dict[str, Any] | None: ...

    def complete_row(self, run_id: str, row_index: int, response_json: str) -> bool: ...

    def fail_row(self, run_id: str, row_index: int, error: str) -> bool: ...

    def finalize_run(self, run_id: str) -> None: ...

    def mark_run_interrupted(self, run_id: str) -> None: ...


def default_answer_fn(query: str, top_n: int, top_k: int) -> dict[str, Any]:
    """生产默认答问实现：直接复用单问服务。"""
    return answer_question(query=query, top_n=top_n, top_k=top_k)


def default_concurrency_provider() -> int:
    """按当前配置计算 run 内并发数；配置不可用时退回串行。"""
    try:
        config = RetrievalConfig.from_env()
    except Exception:
        return 1
    return batch_concurrency(config)


class BatchRunner:
    """跨 run 串行、run 内可并发的批量执行器。"""

    def __init__(
        self,
        store: BatchStoreProtocol | BatchRunStore,
        answer_fn: AnswerFn | None = None,
        concurrency_provider: ConcurrencyProvider | None = None,
    ) -> None:
        self._store = store
        self._answer_fn = answer_fn or default_answer_fn
        self._concurrency_provider = (
            concurrency_provider or default_concurrency_provider
        )
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="xhbx-batch-runner",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
            self._queue.put(None)
            thread.join(timeout=_WORKER_JOIN_TIMEOUT_SECONDS)
            self._thread = None

    def enqueue(self, run_id: str) -> None:
        self._queue.put(run_id)

    def execute_run(self, run_id: str) -> None:
        """同步执行一个批量会话；run 不存在或不可领取时静默跳过。"""
        if not self._store.claim_run(run_id):
            return
        interrupted = False
        try:
            concurrency = max(1, int(self._concurrency_provider()))
            pending_row_indexes = self._store.fetch_pending_row_indexes(run_id)
            if concurrency == 1:
                for row_index in pending_row_indexes:
                    self._execute_row(run_id, row_index)
            else:
                self._execute_rows_concurrently(
                    run_id, pending_row_indexes, concurrency
                )
        except Exception:
            interrupted = True
            logger.exception("批量任务执行中断")
            self._mark_interrupted_best_effort(run_id)
        finally:
            if not interrupted:
                self._store.finalize_run(run_id)

    # ---------------------------------------------------------------- 内部

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            try:
                self.execute_run(item)
            except Exception:
                logger.exception("批量任务执行失败")

    def _execute_rows_concurrently(
        self,
        run_id: str,
        row_indexes: list[int],
        concurrency: int,
    ) -> None:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(self._execute_row, run_id, row_index)
                for row_index in row_indexes
            ]
            for future in futures:
                future.result()

    def _execute_row(self, run_id: str, row_index: int) -> None:
        if not self._store.mark_row_running(run_id, row_index):
            return
        question = self._store.get_question(run_id, row_index)
        if question is None:
            return
        try:
            response = self._answer_fn(
                question["query"],
                question["top_n"],
                question["top_k"],
            )
        except Exception as exc:  # noqa: BLE001 - 行级失败不拖垮整批
            logger.exception("批量行执行失败")
            self._store.fail_row(run_id, row_index, answer_exception_detail(exc))
            return
        self._store.complete_row(
            run_id,
            row_index,
            json.dumps(response, ensure_ascii=False, default=str),
        )

    def _mark_interrupted_best_effort(self, run_id: str) -> None:
        try:
            self._store.mark_run_interrupted(run_id)
        except Exception:
            logger.exception("批量任务中断状态写入失败")
