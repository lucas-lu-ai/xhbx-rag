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
_STRUCTURED_FIELDS = (
    "title",
    "category",
    "scenario",
    "tags",
    "tag_paths",
    "business_categories",
    "business_tags",
    "sales_stages",
    "customer_segments",
    "customer_needs",
    "product_categories",
    "objection_types",
    "compliance_risks",
    "knowledge_type",
    "strategy_name",
    "applicable_stages",
)
_DIRECT_DOMAIN_FIELDS = ("title", "category", "scenario", "tags", "business_domains", "tag_paths")

DOMAIN_KEYWORDS: dict[Domain, tuple[str, ...]] = {
    "产品知识": (
        "保险产品",
        "产品知识",
        "产品",
        "险种产品",
        "险种",
        "保障责任",
        "保险责任",
        "保障方案",
        "条款",
        "保单",
        "投保",
        "承保",
        "核保",
        "保额",
        "保费",
        "保险金",
        "现金价值",
        "退保",
        "续保",
        "受益人",
        "精算",
        "费率",
        "保险期间",
        "缴费期间",
        "养老金",
        "年金",
        "寿险",
        "重疾险",
        "医疗险",
        "意外险",
        "意外伤害保险",
        "医疗保险",
        "重大疾病保险",
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
        "合规风险",
        "合规要求",
        "合规",
        "风控",
        "风险管理",
        "风险控制",
        "风险防控",
        "风险警示",
        "风险教育",
        "风险识别",
        "风险评估",
        "风险揭示",
        "监管",
        "反洗钱",
        "反不正当竞争",
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
        "法律",
        "法规",
        "违规",
        "禁止",
        "道德",
        "诚信",
        "诉讼",
        "纠纷",
        "争议",
        "信息安全",
        "消费者保护",
        "欺诈",
        "信息披露",
        "广告法",
        "保密",
        "定密",
        "五虚",
        "诈骗",
        "失信",
        "侵权",
        "物权",
        "拒赔",
        "涉密",
        "举报",
        "品质管控",
        "品质管理",
        "监督机制",
        "运营规范",
        "警示教育",
        "风险处置",
        "单证规范",
        "财产分割",
    ),
    "销售技能": (
        "销售技能",
        "销售",
        "营销",
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
        "需求",
        "方案设计",
        "方案",
        "成交",
        "促成",
        "邀约",
        "拜访",
        "展业",
        "讲解",
        "推介",
        "推广",
        "价值传递",
        "价值塑造",
        "观念引导",
        "观念导入",
        "理念引导",
        "理念导入",
        "理念培育",
        "观念培育",
        "痛点",
        "竞品",
        "利益演示",
        "价格策略",
        "谈判策略",
        "中场策略",
        "准备阶段",
        "技能训练",
        "模拟演练",
        "通关流程",
        "信任构建",
        "关系构建",
        "破冰交流",
        "社交互动",
        "专业展示",
        "理念建立",
        "规划咨询",
        "专项规划",
        "保险筹划",
        "教育规划",
        "升学",
        "婚前规划",
        "保障规划",
        "信息收集",
    ),
    "客户经营": (
        "客户经营",
        "客户",
        "客群",
        "服务",
        "获客",
        "客户服务",
        "客群管理",
        "客户管理",
        "客户关系",
        "转介绍",
        "关系维护",
        "关系建立",
        "回访",
        "名单",
        "社群",
        "朋友圈",
        "活动运营",
        "活动营销",
        "客户活动",
        "保全",
        "理赔协助",
        "理赔服务",
        "售后",
        "售前",
        "增值服务",
        "高端服务",
        "存量经营",
        "应急保障",
        "会诊",
        "理赔",
        "高净值",
        "客户画像",
        "需求分析",
        "服务经营",
        "客户筛选",
        "孤儿单",
    ),
    "行业与公司": (
        "行业与公司",
        "行业",
        "公司",
        "公司品牌",
        "新华保险",
        "新华",
        "公司介绍",
        "企业文化",
        "保险行业",
        "行业趋势",
        "市场分析",
        "市场",
        "宏观经济",
        "宏观",
        "政策",
        "趋势",
        "社保",
        "医保",
        "保险基础",
        "知识普及",
        "基础知识",
        "基础认知",
        "投资者教育",
        "战略规划",
        "文化建设",
        "社会保障",
        "保险知识",
        "保险原理",
        "金融基础",
        "金融",
        "银行",
        "制度沿革",
        "养老保险",
        "生育保险",
        "儿童福利",
        "权益",
        "品牌",
    ),
    "个人成长": (
        "个人成长",
        "个人",
        "职业",
        "学习",
        "成长",
        "职业素养",
        "素养",
        "自我管理",
        "时间管理",
        "情绪管理",
        "学习能力",
        "专业成长",
        "职业生涯",
        "绩效提升",
        "目标管理",
        "目标",
        "心态",
        "习惯",
        "办公",
        "公文",
        "写作",
        "礼仪",
        "能力建设",
        "健康管理",
        "健康教育",
        "财富管理",
        "财富规划",
        "资产配置",
        "认知构建",
        "认知培育",
        "认知建立",
        "核心分析",
        "数据分析",
        "财务分析",
        "财务规划",
        "财务诊断",
        "财务指标",
        "财务基础",
        "理财",
        "税务计算",
        "现金规划",
        "健康规划",
        "医疗技术",
        "健康知识",
        "疾病科普",
        "心理健康",
        "生活指导",
        "心理支持",
        "情感共鸣",
        "通用能力",
        "专业能力提升",
        "专业形象",
        "能力模型",
        "生涯规划",
        "技术支持",
        "中医",
        "健康与保险",
        "利率",
        "资金时间价值",
    ),
    "组织发展": (
        "组织发展",
        "组织",
        "增员",
        "招募",
        "团队管理",
        "团队",
        "组织管理",
        "主管",
        "人才培养",
        "人才",
        "绩效管理",
        "晨会",
        "早会",
        "会议经营",
        "活动量管理",
        "基础管理",
        "运营管理",
        "培训",
        "讲师",
        "师资",
        "课程",
        "教学",
        "新人",
        "会议",
        "激励",
        "薪酬",
        "晋升",
        "考核",
        "辅导",
        "渠道管理",
        "机构管理",
        "领导",
        "基本法",
        "党课",
        "党建",
        "党员",
        "内勤",
        "队伍",
        "流程管理",
        "教育培训",
        "内部管理",
        "课件",
        "教材",
        "案例萃取",
        "经验萃取",
        "访谈筹备",
        "访谈执行",
        "萃取实施",
        "内容提炼",
        "内容生产",
        "管理赋能",
        "运营执行",
        "过程管理",
        "主持",
        "战略思维",
        "战略管理",
        "战略运营",
        "经营分析",
        "经营复盘",
        "财务管理",
        "单证管理",
        "管理职能",
        "执行监控",
        "实战带教",
        "案例实证",
        "绩优培养",
        "运营支撑",
        "资源制作",
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
        "产品知识": ("产品知识", "产品类别"),
        "合规与风控": ("合规与风控", "法律合规", "合规风险"),
        "销售技能": ("销售技能", "销售阶段"),
        "客户经营": ("客户经营", "客户画像", "客户需求"),
        "行业与公司": ("行业与公司", "公司品牌"),
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
