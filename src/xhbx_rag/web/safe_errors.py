"""问答链路异常到安全中文文案的归一逻辑。

单问路由与批量执行共用同一套白名单，保证错误 detail 不泄漏内部路径、
密钥等敏感信息。
"""

from __future__ import annotations

from ..answer import IncompleteModelOutputError
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
