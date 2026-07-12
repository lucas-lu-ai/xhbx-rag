"""Web 异常到安全中文文案的归一逻辑。

问答、批量执行与入库任务使用固定白名单，保证错误 detail 不泄漏内部路径、
密钥、模型原文等敏感信息。
"""

from __future__ import annotations

from ..answer import IncompleteModelOutputError
from ..atomic_indexer import AtomicIndexError, RollbackPendingError
from .ingestion_pipeline import IngestionPipelineError
from .services import LOCAL_INDEX_UNAVAILABLE_ERROR, REQUIRED_CONFIG_KEYS

SAFE_ANSWER_ERROR_MESSAGES = frozenset(
    {
        "问题不能为空",
        "top_n 必须在 1 到 100 之间",
        "top_k 必须在 1 到 20 之间",
        "top_k 不能大于 top_n",
        "配置解析失败，请检查 .env 中的数值配置。",
    }
)
MISSING_CONFIG_ERROR_PREFIX = "缺少必要环境变量:"
INCOMPLETE_MODEL_OUTPUT_ERROR_DETAIL = "模型输出不完整，已尝试 3 次，请稍后重试。"
UNAVAILABLE_ANSWER_ERROR_DETAIL = "问答服务暂时不可用"
_SAFE_CONFIG_KEYS = frozenset(REQUIRED_CONFIG_KEYS)
SAFE_INGESTION_ERROR_DETAILS = {
    "upload_invalid": "上传文件无效",
    "upload_too_large": "上传文件超过大小限制",
    "parse_failed": "文档解析失败",
    "chunk_failed": "文档切分失败",
    "embedding_failed": "向量生成失败",
    "index_failed": "知识库写入失败",
    "rollback_pending": "知识库恢复尚未完成",
    "service_restarted": "服务重启导致任务中断，请从头重试",
    "storage_unavailable": "任务存储暂时不可用",
}
_PIPELINE_ERROR_CODES = frozenset(
    {"upload_invalid", "upload_too_large", "parse_failed", "chunk_failed"}
)


def is_safe_answer_error(message: str) -> bool:
    """判断问答 ValueError 文案是否可以原样返回给前端。"""
    if message in SAFE_ANSWER_ERROR_MESSAGES:
        return True
    if not message.startswith(MISSING_CONFIG_ERROR_PREFIX):
        return False

    # 防篡改：逐个校验缺失键名，避免异常消息里夹带内部信息被透传。
    raw_keys = message.removeprefix(MISSING_CONFIG_ERROR_PREFIX)
    keys = [item.strip() for item in raw_keys.split(",")]
    return bool(keys) and all(key in _SAFE_CONFIG_KEYS for key in keys)


def answer_exception_detail(exc: Exception) -> str:
    """把问答异常归一为安全中文文案；未知异常一律返回兜底文案。"""
    if isinstance(exc, IncompleteModelOutputError):
        return INCOMPLETE_MODEL_OUTPUT_ERROR_DETAIL
    if isinstance(exc, ValueError):
        message = str(exc)
        if message == LOCAL_INDEX_UNAVAILABLE_ERROR:
            return message
        if is_safe_answer_error(message):
            return message
    return UNAVAILABLE_ANSWER_ERROR_DETAIL


def safe_ingestion_error(code: object) -> tuple[str, str]:
    """把任意入库错误码归一为固定 code/detail。"""
    normalized = str(code)
    if normalized not in SAFE_INGESTION_ERROR_DETAILS:
        normalized = "storage_unavailable"
    return normalized, SAFE_INGESTION_ERROR_DETAILS[normalized]


def ingestion_exception_error(exc: Exception) -> tuple[str, str]:
    """把 Pipeline/AtomicIndexer 异常映射到固定入库错误。"""
    if isinstance(exc, RollbackPendingError):
        return safe_ingestion_error("rollback_pending")
    if isinstance(exc, IngestionPipelineError):
        code = exc.code if exc.code in _PIPELINE_ERROR_CODES else "storage_unavailable"
        return safe_ingestion_error(code)
    if isinstance(exc, AtomicIndexError):
        code = "embedding_failed" if str(exc) == "向量生成失败" else "index_failed"
        return safe_ingestion_error(code)
    return safe_ingestion_error("storage_unavailable")
