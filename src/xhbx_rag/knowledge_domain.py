from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from .models import RagChunk


Domain = Literal[
    "产品知识",
    "合规与风控",
    "销售技能",
    "客户经营",
    "行业与公司",
    "个人成长",
    "组织发展",
]
SourceKind = Literal["培训资料", "绩优案例"]

CANONICAL_DOMAINS: tuple[Domain, ...] = (
    "产品知识",
    "合规与风控",
    "销售技能",
    "客户经营",
    "行业与公司",
    "个人成长",
    "组织发展",
)
DOMAIN_TIE_ORDER: tuple[Domain, ...] = (
    "合规与风控",
    "组织发展",
    "产品知识",
    "客户经营",
    "销售技能",
    "行业与公司",
    "个人成长",
)
DOMAIN_TAGGING_METHOD = "规则匹配"
DOMAIN_TAGGING_VERSION = "2026-07-16"

_SOURCE_KINDS = frozenset({"培训资料", "绩优案例"})
_DOMAIN_SET = frozenset(CANONICAL_DOMAINS)
_STRUCTURED_FIELDS = ("title", "category", "scenario", "tags")
_DIRECT_DOMAIN_FIELDS = ("title", "category", "scenario", "tags", "business_domains", "tag_paths")

DOMAIN_KEYWORDS: dict[Domain, tuple[str, ...]] = {
    "产品知识": (
        "保险产品",
        "产品知识",
        "险种产品",
        "保障责任",
        "保险责任",
        "条款",
        "保单",
        "养老金",
        "年金",
        "寿险",
        "重疾险",
        "医疗险",
        "意外险",
        "长护险",
        "养老险",
        "增额终身寿",
        "分红险",
        "万能险",
        "投连险",
    ),
    "合规与风控": (
        "合规与风控",
        "法律合规",
        "合规要求",
        "合规",
        "风控",
        "监管",
        "反洗钱",
        "销售误导",
        "适当性",
        "双录",
        "隐私",
        "消费者权益",
        "禁止事项",
        "风险提示",
        "民法典",
        "保险法",
        "税法",
        "纠纷",
    ),
    "销售技能": (
        "销售技能",
        "销售切入",
        "产品营销",
        "产品推介",
        "话术",
        "沟通",
        "面谈",
        "促成",
        "异议处理",
        "异议",
        "需求挖掘",
        "需求唤醒",
        "方案设计",
        "成交",
    ),
    "客户经营": (
        "客户经营",
        "客户服务",
        "客群管理",
        "客户管理",
        "客户关系",
        "转介绍",
        "高净值",
        "客户画像",
        "需求分析",
        "服务经营",
        "客户筛选",
        "孤儿单",
    ),
    "行业与公司": (
        "行业与公司",
        "公司品牌",
        "新华保险",
        "公司介绍",
        "企业文化",
        "保险行业",
        "行业趋势",
        "市场分析",
        "宏观经济",
        "品牌",
    ),
    "个人成长": (
        "个人成长",
        "职业素养",
        "自我管理",
        "时间管理",
        "情绪管理",
        "学习能力",
        "专业成长",
        "职业生涯",
        "绩效提升",
        "目标管理",
        "心态",
        "习惯",
    ),
    "组织发展": (
        "组织发展",
        "增员",
        "招募",
        "团队管理",
        "组织管理",
        "主管",
        "人才培养",
        "绩效管理",
        "晨会",
        "早会",
        "会议经营",
        "活动量管理",
        "基础管理",
        "党课",
    ),
}


@dataclass(frozen=True)
class RuleHit:
    domain: Domain
    field: str
    rule: str
    points: int


@dataclass(frozen=True)
class DomainClassification:
    primary_domain: Domain
    domain_tags: list[Domain]
    scores: dict[Domain, int]
    hits: list[RuleHit]


def infer_chunk_domains(chunk: RagChunk) -> DomainClassification | None:
    hits = _dedupe_rule_hits(
        [
            *_direct_domain_hits(chunk.metadata),
            *_keyword_hits(_structured_text(chunk.metadata), "metadata", 4),
            *_keyword_hits(_source_text(chunk), "source", 2),
            *_keyword_hits(chunk.text, "text", 1),
        ]
    )
    scores = _scores(hits)
    tags = [domain for domain in DOMAIN_TIE_ORDER if scores.get(domain, 0) >= 4]
    if not tags:
        return None
    primary = min(
        tags,
        key=lambda domain: (-scores[domain], DOMAIN_TIE_ORDER.index(domain)),
    )
    ordered = [primary, *[domain for domain in tags if domain != primary]]
    return DomainClassification(
        primary_domain=primary,
        domain_tags=ordered,
        scores={domain: scores[domain] for domain in DOMAIN_TIE_ORDER if domain in scores},
        hits=hits,
    )


def infer_query_domains(text: str) -> list[Domain]:
    matched = {
        hit.domain
        for hit in _keyword_hits(text, field="query", points=1)
    }
    return [domain for domain in DOMAIN_TIE_ORDER if domain in matched]


def apply_domain_metadata(
    chunk: RagChunk,
    classification: DomainClassification,
    source_kind: SourceKind,
) -> RagChunk:
    metadata = dict(chunk.metadata)
    metadata.update(
        {
            "source_kind": source_kind,
            "primary_domain": classification.primary_domain,
            "domain_tags": list(classification.domain_tags),
            "domain_tagging_method": DOMAIN_TAGGING_METHOD,
            "domain_tagging_version": DOMAIN_TAGGING_VERSION,
        }
    )
    return chunk.model_copy(update={"metadata": metadata})


def validate_domain_metadata(metadata: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    source_kind = metadata.get("source_kind")
    primary_domain = metadata.get("primary_domain")
    raw_tags = metadata.get("domain_tags")

    if source_kind not in _SOURCE_KINDS:
        errors.append("source_kind 必须是 培训资料 或 绩优案例")
    if primary_domain not in _DOMAIN_SET:
        errors.append("primary_domain 不是允许的一级标签")
    if not isinstance(raw_tags, list) or not raw_tags:
        errors.append("domain_tags 必须是非空列表")
        tags: list[object] = []
    else:
        tags = raw_tags
        if len(tags) != len({str(item) for item in tags}):
            errors.append("domain_tags 必须去重")
        invalid = [str(item) for item in tags if item not in _DOMAIN_SET]
        if invalid:
            errors.append(f"domain_tags 包含不支持的一级标签: {', '.join(invalid)}")
        expected_order = sorted(
            [item for item in tags if item in _DOMAIN_SET],
            key=lambda item: (
                item != primary_domain,
                DOMAIN_TIE_ORDER.index(cast(Domain, item)),
            ),
        )
        if tags != expected_order and not invalid:
            errors.append("domain_tags 顺序不稳定")
    if primary_domain in _DOMAIN_SET and primary_domain not in tags:
        errors.append("primary_domain 必须包含在 domain_tags 中")
    if metadata.get("domain_tagging_method") != DOMAIN_TAGGING_METHOD:
        errors.append(f"domain_tagging_method 必须是 {DOMAIN_TAGGING_METHOD}")
    if metadata.get("domain_tagging_version") != DOMAIN_TAGGING_VERSION:
        errors.append(f"domain_tagging_version 必须是 {DOMAIN_TAGGING_VERSION}")
    return errors


def _direct_domain_hits(metadata: dict[str, Any]) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for field in _DIRECT_DOMAIN_FIELDS:
        for value in _strings(metadata.get(field)):
            for domain in CANONICAL_DOMAINS:
                if _contains_domain_label(value, domain):
                    hits.append(RuleHit(domain, field, f"direct:{domain}", 10))
    return hits


def _contains_domain_label(value: str, domain: Domain) -> bool:
    aliases = {
        "行业与公司": ("行业与公司", "公司品牌"),
        "合规与风控": ("合规与风控", "法律合规"),
    }.get(domain, (domain,))
    if any(alias == value for alias in aliases):
        return True
    return any(value.startswith(f"{alias}/") for alias in aliases)


def _structured_text(metadata: dict[str, Any]) -> str:
    values = [
        item
        for field in _STRUCTURED_FIELDS
        for item in _strings(metadata.get(field))
        if not any(_contains_domain_label(item, domain) for domain in CANONICAL_DOMAINS)
    ]
    return "\n".join(values)


def _source_text(chunk: RagChunk) -> str:
    filenames = [citation.filename for citation in chunk.citations if citation.filename]
    return "\n".join([chunk.source_file, *filenames])


def _keyword_hits(text: str, field: str, points: int) -> list[RuleHit]:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return []
    return [
        RuleHit(domain=domain, field=field, rule=keyword, points=points)
        for domain, keywords in DOMAIN_KEYWORDS.items()
        for keyword in keywords
        if keyword.lower() in normalized
    ]


def _dedupe_rule_hits(hits: list[RuleHit]) -> list[RuleHit]:
    deduped: list[RuleHit] = []
    seen: set[tuple[Domain, str, str]] = set()
    for hit in hits:
        key = (hit.domain, hit.field, hit.rule)
        if key not in seen:
            seen.add(key)
            deduped.append(hit)
    return deduped


def _scores(hits: list[RuleHit]) -> dict[Domain, int]:
    scores: dict[Domain, int] = {}
    for hit in hits:
        scores[hit.domain] = scores.get(hit.domain, 0) + hit.points
    return scores


def _strings(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []
