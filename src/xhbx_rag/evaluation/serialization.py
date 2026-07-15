from typing import Any

from pydantic import BaseModel

_ANSWER_KEY_ALIASES = {
    # answer_question() 顶层字段
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
    # 查询理解过滤器
    "chunk_types": "知识类型过滤",
    "stage": "销售阶段",
    "scenario": "场景",
    "objection": "客户异议",
    "strategy_names": "策略名称列表",
    # 检索命中及标签加权
    "rerank_score": "重排得分",
    "matched_tag_paths": "命中标签路径",
    "tag_boost_factor": "标签加权系数",
    # EvidenceRef 与 UI 引用字段
    "section_name": "章节名称",
    "source_id": "来源ID",
    "filename": "文件名",
    "source_type": "来源类型",
    "context": "上下文",
    "locator_confidence": "定位置信度",
    "locator_error": "定位错误",
    "anchor_id": "锚点ID",
    # 案例 chunk metadata
    "case_name": "案例名称",
    "customer_state": "客户状态",
    "strategy_name": "策略名称",
    "aliases": "别名",
    "applicable_stages": "适用阶段",
    "confidence": "置信度",
    "inferred": "模型归纳",
    "script_id": "话术ID",
    "customer_trigger": "客户触发点",
    "related_strategy_names": "关联策略名称列表",
    "related_script_ids": "关联话术ID列表",
    "related_script_details": "关联话术详情",
    "goal": "目标",
    "source_quote": "原始话术",
    "coach_wording": "教练推荐话术",
    "follow_up_questions": "追问建议",
    "compliance_notes": "合规提醒",
    # 通用标签 metadata
    "knowledge_type": "知识类型",
    "tag_paths": "标签路径",
    "business_domains": "业务领域",
    "business_categories": "业务分类",
    "business_tags": "业务标签",
    "sales_stages": "销售阶段列表",
    "customer_segments": "客户分群",
    "customer_needs": "客户需求",
    "product_categories": "产品类别",
    "objection_types": "异议类型",
    "compliance_risks": "合规风险",
    "tagging_method": "打标方法",
    "tagging_version": "打标版本",
    # 课程 chunk metadata
    "course_name": "课程名称",
    "course_series": "课程体系",
    "audience": "适用对象",
    "summary": "摘要",
    "slide_start": "幻灯片起始页",
    "slide_end": "幻灯片结束页",
    "page_start": "页码起始",
    "page_end": "页码结束",
    "heading": "标题",
    "teaching_goals": "教学目标",
    # citation locator
    "container": "容器路径",
    "page": "页码",
    "slide": "幻灯片页码",
    "line_start": "起始行",
    "line_end": "结束行",
    "char_start": "起始字符位置",
    "char_end": "结束字符位置",
    "heading_path": "标题路径",
}
_TECHNICAL_KEY_ALLOWLIST = frozenset({"chunk_id"})


def _map_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _ANSWER_KEY_ALIASES.get(key, key): _map_keys(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_map_keys(item) for item in value]
    return value


def _contains_english_business_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (
                isinstance(key, str)
                and key not in _TECHNICAL_KEY_ALLOWLIST
                and key.isascii()
                and any(character.isalpha() for character in key)
            )
            or _contains_english_business_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_english_business_key(item) for item in value)
    return False


def dump_chinese(model: BaseModel) -> dict[str, Any]:
    payload = _map_keys(model.model_dump(mode="json", by_alias=True))
    if _contains_english_business_key(payload):
        raise ValueError("对外结果包含英文业务字段")
    return payload
