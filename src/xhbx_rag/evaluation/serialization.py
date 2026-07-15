from typing import Any

from pydantic import BaseModel

_ANSWER_KEY_ALIASES = {
    "original_query": "原始问题",
    "rewritten_query": "改写问题",
    "intent": "意图",
    "filters": "过滤条件",
    "answer": "智能体回答",
    "reasoning": "思考过程",
    "citations": "引用",
    "evidence_count": "证据数量",
    "retrieval_evidences": "检索证据",
    "chunk_type": "chunk类型",
    "text": "证据正文",
    "metadata": "元数据",
    "score": "检索得分",
    "evidence_index": "证据序号",
    "source_path": "来源路径",
    "locator": "来源定位",
    "source_excerpt": "原文摘录",
    "quote": "引用原文",
    "display_location": "展示定位",
    "display_excerpt": "展示摘录",
    "can_reveal": "可查看源文件",
    "selected": "模型选中",
}
_FORBIDDEN_KEYS = {
    "correctness_score",
    "passed",
    "failed",
    "unsupported_claims",
}


def _map_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _ANSWER_KEY_ALIASES.get(key, key): _map_keys(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_map_keys(item) for item in value]
    return value


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            key in _FORBIDDEN_KEYS or _contains_forbidden_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def dump_chinese(model: BaseModel) -> dict[str, Any]:
    payload = _map_keys(model.model_dump(mode="json", by_alias=True))
    if _contains_forbidden_key(payload):
        raise ValueError("对外结果包含英文业务字段")
    return payload
