from __future__ import annotations

from queue import Queue
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Iterator, Mapping

from xhbx_rag.answer import AnswerAgent, answer_query
from xhbx_rag.config import ConfigError, RetrievalConfig, load_env_values
from xhbx_rag.embedding import EmbeddingClient
from xhbx_rag.milvus_store import create_milvus_store
from xhbx_rag.observability import (
    CompositeTraceSink,
    TraceSink,
    close_trace,
    create_studio_trace_sink,
)
from xhbx_rag.query_understanding import QueryUnderstandingAgent
from xhbx_rag.rerank import RerankClient
from xhbx_rag.resource_utils import close_resources, is_local_index_open_failure

from .source_paths import (
    can_reveal_source,
    citation_display_excerpt,
    display_location,
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
LOCAL_INDEX_UNAVAILABLE_ERROR = (
    "本地 Milvus 索引暂时不可用，请关闭其他正在使用索引的进程后重试。"
)
WEB_STUDIO_TRACE_ROOT_NAME = "xhbx-rag.web.answer"
WEB_STUDIO_TRACE_TRUTHY = {"1", "true", "yes", "on"}
# 批量执行并发数：Milvus Lite 是本地文件、并发打开会失败，仅 docker 模式允许 >1。
SERIAL_BATCH_CONCURRENCY = 1
DEFAULT_DOCKER_BATCH_CONCURRENCY = 3
MAX_BATCH_CONCURRENCY = 10
# 数据目录固定位于项目根下的 data/，展示相对路径即可，不暴露绝对路径。
DATA_DIR_DISPLAY = "data"
# Milvus Lite 是单进程文件库：进程内所有问答（单问 + 批量行）共用一把锁串行化。
_LITE_ANSWER_LOCK = Lock()


def get_status() -> dict[str, Any]:
    try:
        config = RetrievalConfig.from_env()
    except ConfigError as exc:
        return {
            "ok": False,
            "data_dir": DATA_DIR_DISPLAY,
            "milvus_mode": "",
            "milvus_target": "",
            "milvus_lite_path": "",
            "milvus_collection": "",
            "batch_concurrency": SERIAL_BATCH_CONCURRENCY,
            "config": _missing_config_map(str(exc)),
            "errors": [str(exc)],
        }
    except ValueError:
        return {
            "ok": False,
            "data_dir": DATA_DIR_DISPLAY,
            "milvus_mode": "",
            "milvus_target": "",
            "milvus_lite_path": "",
            "milvus_collection": "",
            "batch_concurrency": SERIAL_BATCH_CONCURRENCY,
            "config": _missing_config_map(SAFE_CONFIG_PARSE_ERROR),
            "errors": [SAFE_CONFIG_PARSE_ERROR],
        }

    return {
        "ok": True,
        "data_dir": DATA_DIR_DISPLAY,
        "milvus_mode": config.milvus_mode,
        "milvus_target": _milvus_target(config),
        "milvus_lite_path": str(config.milvus_lite_path),
        "milvus_collection": config.milvus_collection,
        "batch_concurrency": batch_concurrency(config),
        "config": {key: True for key in REQUIRED_CONFIG_KEYS},
        "errors": [],
    }


def batch_concurrency(config: RetrievalConfig) -> int:
    if config.milvus_mode != "docker":
        return SERIAL_BATCH_CONCURRENCY

    raw_value = load_env_values().get("WEB_BATCH_CONCURRENCY", "").strip()
    if not raw_value:
        return DEFAULT_DOCKER_BATCH_CONCURRENCY
    try:
        parsed = int(raw_value)
    except ValueError:
        return DEFAULT_DOCKER_BATCH_CONCURRENCY
    return max(SERIAL_BATCH_CONCURRENCY, min(MAX_BATCH_CONCURRENCY, parsed))


# 兼容旧名，避免存量调用点/测试破坏。
_batch_concurrency = batch_concurrency


def answer_question(
    *,
    query: str,
    top_n: int,
    top_k: int,
    project_root: Path | None = None,
    trace: TraceSink | None = None,
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

    if config.milvus_mode == "lite":
        # Lite 模式下本地索引不支持并发打开，从资源构建到关闭全程持锁。
        with _LITE_ANSWER_LOCK:
            return _answer_question_with_config(
                config=config,
                query=stripped_query,
                top_n=top_n,
                top_k=top_k,
                project_root=project_root,
                trace=trace,
            )
    return _answer_question_with_config(
        config=config,
        query=stripped_query,
        top_n=top_n,
        top_k=top_k,
        project_root=project_root,
        trace=trace,
    )


def _answer_question_with_config(
    *,
    config: RetrievalConfig,
    query: str,
    top_n: int,
    top_k: int,
    project_root: Path | None = None,
    trace: TraceSink | None = None,
) -> dict[str, Any]:
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
            if config.milvus_mode == "lite" and _is_local_index_open_failure(exc):
                raise ValueError(LOCAL_INDEX_UNAVAILABLE_ERROR) from exc
            raise
        resources.append(store)
        reranker = RerankClient(
            base_url=config.rerank_base_url,
            api_key=config.rerank_api_key,
            model=config.rerank_model_name,
        )
        resources.append(reranker)
        raw_answer_agent = AnswerAgent(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model_name,
        )
        resources.append(raw_answer_agent)
        answer_agent = _RecordingAnswerAgent(raw_answer_agent)
        result = answer_query(
            query=query,
            query_agent=query_agent,
            embedding_client=embedding_client,
            store=store,
            reranker=reranker,
            answer_agent=answer_agent,
            top_n=top_n,
            top_k=top_k,
            trace=trace,
        )
    finally:
        _close_resources(resources)

    normalized = dict(result)
    normalized["citations"] = [
        _citation_for_ui(citation, project_root=project_root)
        for citation in result.get("citations", []) or []
    ]
    normalized["retrieval_evidences"] = _retrieval_evidences_for_ui(
        answer_agent.search_result,
        project_root=project_root,
    )
    return normalized


def answer_question_stream_events(
    *,
    query: str,
    top_n: int,
    top_k: int,
    project_root: Path | None = None,
) -> Iterator[dict[str, Any]]:
    events: Queue[dict[str, Any] | object] = Queue()
    done = object()

    def run_answer() -> None:
        trace: TraceSink | None = None
        try:
            trace = _web_stream_trace_sink(events)
            response = answer_question(
                query=query,
                top_n=top_n,
                top_k=top_k,
                project_root=project_root,
                trace=trace,
            )
            events.put({"type": "_result", "response": response})
        except Exception as exc:  # noqa: BLE001 - converted to safe SSE at route boundary
            events.put({"type": "_exception", "exception": exc})
        finally:
            close_trace(trace)
            events.put(done)

    Thread(target=run_answer, daemon=True).start()

    while True:
        event = events.get()
        if event is done:
            break
        if not isinstance(event, dict):
            continue
        if event.get("type") == "_result":
            response = event["response"]
            if isinstance(response, Mapping):
                for text in _answer_delta_chunks(str(response.get("answer", ""))):
                    yield {"type": "answer_delta", "text": text}
                yield {"type": "final", "response": dict(response)}
            continue
        yield event


class _QueueTraceSink:
    def __init__(self, events: Queue[dict[str, Any] | object]) -> None:
        self.events = events

    def emit(self, step: str, payload: Mapping[str, Any]) -> None:
        self.events.put(
            {
                "type": "step",
                "step": step,
                "message": _stream_step_message(step),
                "payload": _trace_payload_for_ui(step, payload),
            }
        )


class _BestEffortTraceSink:
    def __init__(self, sink: TraceSink) -> None:
        self.sink = sink

    def emit(self, step: str, payload: Mapping[str, Any]) -> None:
        try:
            self.sink.emit(step, payload)
        except Exception:
            return

    def close(self) -> None:
        try:
            close_trace(self.sink)
        except Exception:
            return


def _web_stream_trace_sink(events: Queue[dict[str, Any] | object]) -> TraceSink:
    queue_trace = _QueueTraceSink(events)
    enabled, endpoint = _web_studio_trace_config()
    if not enabled:
        return queue_trace
    try:
        studio_trace = create_studio_trace_sink(
            endpoint=endpoint,
            root_name=WEB_STUDIO_TRACE_ROOT_NAME,
        )
    except Exception:
        return queue_trace
    return CompositeTraceSink([queue_trace, _BestEffortTraceSink(studio_trace)])


def _web_studio_trace_config() -> tuple[bool, str]:
    values = load_env_values()
    enabled_value = values.get("WEB_STUDIO_TRACE", "").strip().lower()
    endpoint = values.get("WEB_STUDIO_ENDPOINT", "localhost:4317").strip()
    return enabled_value in WEB_STUDIO_TRACE_TRUTHY, endpoint or "localhost:4317"


_STREAM_STEP_MESSAGES = {
    "search.query_received": "已收到问题",
    "search.query_understood": "已完成问题理解",
    "search.skipped": "已跳过检索",
    "search.query_embedded": "已生成检索向量",
    "search.vector_searched": "已完成向量检索",
    "search.keyword_searched": "已完成关键词检索",
    "search.hybrid_fused": "已完成混合召回融合",
    "search.tag_boosted": "已完成标签加权",
    "search.reranked": "已完成证据重排",
    "search.completed": "已完成检索",
    "answer.skipped": "已跳过回答生成",
    "answer.generated": "已完成回答生成",
}


def _stream_step_message(step: str) -> str:
    return _STREAM_STEP_MESSAGES.get(step, step)


def _trace_payload_for_ui(step: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    ui_payload = dict(payload)
    if step == "search.query_embedded":
        ui_payload.pop("vector_head", None)
    if "candidates" in ui_payload:
        ui_payload.pop("candidates", None)
    if step == "answer.generated":
        ui_payload.pop("answer_preview", None)
    return ui_payload


def _answer_delta_chunks(answer: str, *, chunk_size: int = 18) -> Iterator[str]:
    buffer = ""
    for char in answer:
        buffer += char
        if char in "。！？；\n" or len(buffer) >= chunk_size:
            yield buffer
            buffer = ""
    if buffer:
        yield buffer


def _milvus_target(config: RetrievalConfig) -> str:
    if config.milvus_mode == "docker":
        return config.milvus_uri
    return str(config.milvus_lite_path)


class _RecordingAnswerAgent:
    def __init__(self, agent: object) -> None:
        self.agent = agent
        self.search_result: Mapping[str, Any] | None = None

    def generate(self, search_result: Mapping[str, Any]) -> object:
        self.search_result = search_result
        return self.agent.generate(search_result)  # type: ignore[attr-defined]


def _retrieval_evidences_for_ui(
    search_result: Mapping[str, Any] | None,
    *,
    project_root: Path | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(search_result, Mapping):
        return []

    evidences: list[dict[str, Any]] = []
    for item in search_result.get("results", []) or []:
        if not isinstance(item, Mapping):
            continue
        ui_item = dict(item)
        raw_citations = item.get("citations", []) or []
        if not isinstance(raw_citations, list):
            raw_citations = []
        ui_item["citations"] = [
            _citation_for_ui(citation, project_root=project_root)
            for citation in raw_citations
        ]
        evidences.append(ui_item)
    return evidences


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


# 资源关闭与 lite 占用判定逻辑与 MCP 服务面共用，统一放在 resource_utils。
_close_resources = close_resources


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


_is_local_index_open_failure = is_local_index_open_failure
