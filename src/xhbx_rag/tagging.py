from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .models import EvidenceRef, RagChunk


TAGGING_VERSION = "2026-07-03"

KNOWLEDGE_TYPE_BY_CHUNK_TYPE = {
    "customer_journey": "客户旅程",
    "strategy": "销售策略",
    "script": "场景话术",
    "objection_handling": "异议处理",
    "training_course": "培训课程",
}

_TEXT_LIST_FIELDS = (
    "stage",
    "scenario",
    "customer_trigger",
    "strategy_name",
    "objection",
    "customer_state",
)

_BUSINESS_TAG_RULES = (
    (
        ("高净值", "资产配置", "财富传承", "家族", "企业主"),
        "客户经营/特殊客户服务/高净值客户服务",
    ),
    (
        ("保险理念", "风险意识", "风险唤醒", "家庭责任", "保险功能", "风险科普"),
        "销售技能/沟通谈判/保险理念沟通",
    ),
    (
        ("开场白", "破冰", "初次接触", "初步需求"),
        "销售技能/沟通谈判/初次沟通技巧",
    ),
    (
        ("倾听", "提问", "深度沟通", "表达需求"),
        "销售技能/沟通谈判/深度沟通技巧",
    ),
    (
        ("缺口", "保障缺口", "保额", "风险缺口"),
        "销售技能/需求分析/保障缺口测算",
    ),
    (
        ("画像", "年龄", "职业", "家庭结构", "风险偏好"),
        "销售技能/需求分析/客户画像匹配",
    ),
    (
        ("方案", "组合", "定制", "规划", "适配"),
        "销售技能/方案设计/定制化方案设计",
    ),
    (
        ("预算", "保费", "交费", "缴费"),
        "销售技能/异议处理/保费异议处理",
    ),
    (
        ("保障范围", "责任", "免责", "保障责任"),
        "销售技能/异议处理/保障范围异议处理",
    ),
    (
        ("理赔", "赔付", "赔款", "理赔难", "理赔慢"),
        "销售技能/异议处理/理赔顾虑异议处理",
    ),
    (
        ("保单年检", "保单检视", "保单整理"),
        "客户经营/售后服务/保单年检",
    ),
    (
        ("转介绍", "推荐客户", "介绍朋友"),
        "销售技能/客户维护/转介绍",
    ),
)

_SALES_STAGE_RULES = (
    (("获客", "引流", "陌生拜访", "短视频", "社群", "直播"), "获客"),
    (("接触", "破冰", "开场白", "初次沟通"), "接触破冰"),
    (("需求唤醒", "风险唤醒", "保险理念"), "需求唤醒"),
    (("需求分析", "需求诊断", "保障缺口", "客户画像"), "需求分析"),
    (("方案设计", "方案", "产品组合", "保障规划"), "方案设计"),
    (("异议", "预算", "理赔难", "保费"), "异议处理"),
    (("促成", "签约", "成交"), "促成签约"),
    (("投保", "健康告知", "保费缴纳"), "投保办理"),
    (("保单递送", "递送"), "保单递送"),
    (("售后", "理赔", "保全", "续期"), "售后服务"),
    (("客户维护", "回访", "节日维护"), "客户维护"),
    (("转介绍", "推荐客户"), "转介绍"),
    (("复购", "加保", "二次挖掘"), "复购加保"),
)

_CUSTOMER_SEGMENT_RULES = (
    (("高净值",), "高净值客户"),
    (("小微企业主",), "小微企业主"),
    (("企业主",), "企业主"),
    (("个体工商户",), "个体工商户"),
    (("工薪",), "工薪家庭"),
    (("年轻家庭",), "年轻家庭"),
    (("二孩",), "二孩家庭"),
    (("单亲",), "单亲家庭"),
    (("银发",), "银发客户"),
    (("老人", "老年"), "老人客户"),
    (("少儿", "孩子", "子女"), "少儿客户家庭"),
    (("已有保单", "既有保单"), "既有保单客户"),
    (("空白保障", "没有保险"), "空白保障客户"),
    (("老客户", "存量客户"), "存量老客户"),
    (("退保", "流失"), "流失风险客户"),
)

_CUSTOMER_NEED_RULES = (
    (("基础保障",), "基础保障"),
    (("医疗", "健康", "住院"), "医疗健康"),
    (("重疾", "重大疾病", "癌症"), "重疾风险"),
    (("身故",), "身故责任"),
    (("意外",), "意外风险"),
    (("预算", "保费", "交费能力"), "保费预算"),
    (("教育金", "孩子教育", "子女教育"), "子女教育"),
    (("养老", "退休", "养老金"), "养老规划"),
    (("财富传承", "传承", "继承", "家族"), "财富传承"),
    (("资产隔离", "隔离", "企业资产", "家庭资产", "债务"), "资产隔离"),
    (("企业经营", "经营风险"), "企业经营风险"),
    (("现金流",), "现金流规划"),
    (("保单整理", "保单检视", "保单年检"), "保单整理"),
    (("家庭责任", "责任期"), "家庭责任"),
    (("保障缺口", "风险缺口"), "保障缺口"),
    (("续期",), "续期缴费"),
    (("理赔",), "理赔服务"),
    (("增值服务", "康养", "医疗资源"), "增值服务"),
)

_PRODUCT_CATEGORY_RULES = (
    (("增额", "增额寿", "终身寿"), "增额终身寿"),
    (("年金", "教育金", "养老金"), "年金险"),
    (("重疾", "重大疾病"), "重疾险"),
    (("医疗", "住院医疗"), "医疗险"),
    (("意外",), "意外险"),
    (("寿险", "定寿", "终身寿"), "寿险"),
    (("养老险",), "养老险"),
    (("护理险",), "护理险"),
    (("分红",), "分红险"),
    (("万能",), "万能险"),
    (("团体保险", "团险"), "团体保险"),
)

_OBJECTION_TYPE_RULES = (
    (("保费", "贵", "性价比"), "保费异议"),
    (("预算",), "预算异议"),
    (("收益低", "收益不高", "回报低", "回报不高"), "收益异议"),
    (("保障范围", "责任", "免责"), "保障范围异议"),
    (("理赔难", "理赔慢", "理赔"), "理赔顾虑"),
    (("必要", "不需要", "没必要"), "必要性异议"),
    (("已有保险", "买过保险", "已经有"), "已有保险异议"),
    (("家人反对", "家里人不同意"), "家人反对"),
    (("考虑一下", "再说", "以后再看"), "再考虑一下"),
    (("不信任", "骗人", "信不过"), "信任异议"),
    (("健康告知",), "健康告知顾虑"),
    (("流动性", "取不出来"), "流动性顾虑"),
    (("缴费期", "交费期"), "缴费期顾虑"),
    (("复杂", "看不懂"), "产品复杂度顾虑"),
)

_COMPLIANCE_RISK_RULES = (
    (("保证收益", "稳赚", "一定收益", "承诺收益", "收益一定"), "收益承诺风险"),
    (("全都能保", "什么都保", "全覆盖"), "夸大保障风险"),
    (("一定赔", "肯定赔", "保证理赔"), "理赔承诺风险"),
    (("最好", "唯一", "一定适合"), "适当性风险"),
    (("治疗", "诊断", "用药"), "医疗建议风险"),
    (("避税", "税务筹划", "法律安排"), "税务法律建议风险"),
    (("身份证", "手机号", "银行卡", "隐私"), "隐私信息风险"),
    (("竞品", "别家公司", "其他公司都不如"), "竞品比较风险"),
    (("返钱", "返佣", "夸大"), "误导销售风险"),
    (("适合所有人", "都适合"), "产品适配风险"),
)


def tag_chunk(chunk: RagChunk) -> RagChunk:
    metadata = dict(chunk.metadata)
    existing_tag_paths = _existing_tag_paths(metadata, chunk.text)
    tags = infer_tags(
        text=chunk.text,
        metadata=metadata,
        citations=chunk.citations,
        chunk_type=chunk.chunk_type,
        existing_tag_paths=existing_tag_paths,
    )
    metadata.update(tags)

    text_without_tag_line = _remove_tag_lines(chunk.text)
    tag_line = render_tag_line(metadata)
    if tag_line:
        text = _insert_tag_line(text_without_tag_line, tag_line)
    else:
        text = text_without_tag_line
    return chunk.model_copy(update={"text": text, "metadata": metadata})


def infer_tags(
    *,
    text: str,
    metadata: dict[str, Any],
    citations: list[EvidenceRef],
    chunk_type: str,
    existing_tag_paths: list[str] | None = None,
) -> dict[str, Any]:
    haystack = _combined_text(text, metadata, citations)
    knowledge_type = KNOWLEDGE_TYPE_BY_CHUNK_TYPE.get(chunk_type, "")

    tag_paths = list(existing_tag_paths or [])
    tag_paths.extend(_paths_from_rules(haystack, _BUSINESS_TAG_RULES))

    sales_stages = _values_from_rules(haystack, _SALES_STAGE_RULES)
    customer_segments = _values_from_rules(haystack, _CUSTOMER_SEGMENT_RULES)
    customer_needs = _values_from_rules(haystack, _CUSTOMER_NEED_RULES)
    product_categories = _values_from_rules(haystack, _PRODUCT_CATEGORY_RULES)
    objection_types = _values_from_rules(haystack, _OBJECTION_TYPE_RULES)
    compliance_risks = _values_from_rules(haystack, _COMPLIANCE_RISK_RULES)

    tag_paths.extend(f"销售阶段/{value}" for value in sales_stages)
    tag_paths.extend(f"客户画像/{value}" for value in customer_segments)
    tag_paths.extend(f"客户需求/{value}" for value in customer_needs)
    tag_paths.extend(f"险种产品/{value}" for value in product_categories)
    tag_paths.extend(f"异议类型/{value}" for value in objection_types)
    tag_paths.extend(f"合规风险/{value}" for value in compliance_risks)
    tag_paths = _dedupe(tag_paths)

    derived = _derive_metadata_from_paths(tag_paths)
    return {
        "knowledge_type": knowledge_type,
        "tag_paths": tag_paths,
        "business_domains": derived["business_domains"],
        "business_categories": derived["business_categories"],
        "business_tags": derived["business_tags"],
        "sales_stages": _dedupe(
            [*sales_stages, *_str_list(metadata.get("sales_stages"))]
        ),
        "customer_segments": _dedupe(
            [*customer_segments, *_str_list(metadata.get("customer_segments"))]
        ),
        "customer_needs": _dedupe(
            [*customer_needs, *_str_list(metadata.get("customer_needs"))]
        ),
        "product_categories": _dedupe(
            [*product_categories, *_str_list(metadata.get("product_categories"))]
        ),
        "objection_types": _dedupe(
            [*objection_types, *_str_list(metadata.get("objection_types"))]
        ),
        "compliance_risks": _dedupe(
            [*compliance_risks, *_str_list(metadata.get("compliance_risks"))]
        ),
        "tagging_method": "规则匹配",
        "tagging_version": TAGGING_VERSION,
    }


def infer_query_tags(text: str) -> list[str]:
    """对查询文本套用与 chunk 相同的打标规则，返回标签路径。

    不含合规风险维度：合规标签用于回答侧提示，不参与召回加权。
    """
    haystack = text.lower()
    if not haystack.strip():
        return []
    paths = list(_paths_from_rules(haystack, _BUSINESS_TAG_RULES))
    paths.extend(
        f"销售阶段/{value}" for value in _values_from_rules(haystack, _SALES_STAGE_RULES)
    )
    paths.extend(
        f"客户画像/{value}"
        for value in _values_from_rules(haystack, _CUSTOMER_SEGMENT_RULES)
    )
    paths.extend(
        f"客户需求/{value}" for value in _values_from_rules(haystack, _CUSTOMER_NEED_RULES)
    )
    paths.extend(
        f"险种产品/{value}"
        for value in _values_from_rules(haystack, _PRODUCT_CATEGORY_RULES)
    )
    paths.extend(
        f"异议类型/{value}"
        for value in _values_from_rules(haystack, _OBJECTION_TYPE_RULES)
    )
    return _dedupe(paths)


def render_tag_line(metadata: dict[str, Any]) -> str:
    tag_paths = _str_list(metadata.get("tag_paths"))
    if not tag_paths:
        return ""
    return "标签：" + "；".join(tag_paths)


def _combined_text(
    text: str,
    metadata: dict[str, Any],
    citations: list[EvidenceRef],
) -> str:
    parts = [text]
    for key in _TEXT_LIST_FIELDS:
        value = metadata.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value:
            parts.append(str(value))
    for citation in citations:
        parts.extend(
            [
                citation.quote,
                citation.context,
                citation.source_excerpt,
            ]
        )
    return "\n".join(part for part in parts if part).lower()


def _paths_from_rules(
    haystack: str,
    rules: Iterable[tuple[tuple[str, ...], str]],
) -> list[str]:
    return [
        path
        for keywords, path in rules
        if any(keyword.lower() in haystack for keyword in keywords)
    ]


def _values_from_rules(
    haystack: str,
    rules: Iterable[tuple[tuple[str, ...], str]],
) -> list[str]:
    return [
        value
        for keywords, value in rules
        if any(keyword.lower() in haystack for keyword in keywords)
    ]


def _derive_metadata_from_paths(tag_paths: list[str]) -> dict[str, list[str]]:
    business_domains: list[str] = []
    business_categories: list[str] = []
    business_tags: list[str] = []
    for path in tag_paths:
        parts = path.split("/")
        if len(parts) < 3 or parts[0] not in {"销售技能", "客户经营"}:
            continue
        business_domains.append(parts[0])
        business_categories.append(parts[1])
        business_tags.append(parts[2])
    return {
        "business_domains": _dedupe(business_domains),
        "business_categories": _dedupe(business_categories),
        "business_tags": _dedupe(business_tags),
    }


def _existing_tag_paths(metadata: dict[str, Any], text: str) -> list[str]:
    paths = _str_list(metadata.get("tag_paths"))
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("标签："):
            continue
        tag_text = stripped.removeprefix("标签：")
        paths.extend(part.strip() for part in tag_text.split("；") if part.strip())
    return _dedupe(paths)


def _remove_tag_lines(text: str) -> str:
    lines = [
        line
        for line in text.splitlines()
        if not line.strip().startswith("标签：")
    ]
    return "\n".join(lines).strip()


def _insert_tag_line(text: str, tag_line: str) -> str:
    lines = text.splitlines()
    if not lines:
        return tag_line
    for index, line in enumerate(lines):
        if line.startswith("知识类型："):
            return "\n".join([*lines[: index + 1], tag_line, *lines[index + 1 :]])
    return "\n".join([lines[0], tag_line, *lines[1:]])


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
