from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ERROR_TAGS = (
    "事实错误",
    "关键点缺失",
    "无依据扩写",
    "答非所问",
    "引用缺失",
    "检索未命中",
    "问答执行失败",
    "裁判执行失败",
)
EvaluationGrade = Literal["优秀", "合格", "不合格", "问答失败", "评测失败"]
TraceStatus = Literal["完整支持", "部分支持", "未定位"]


def _validated_error_tags(values: list[str]) -> list[str]:
    invalid = [value for value in values if value not in ERROR_TAGS]
    if invalid:
        raise ValueError(f"不支持的错误标签: {', '.join(invalid)}")
    return values


class ChineseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class GoldEvidence(ChineseModel):
    chunk_id: str = Field(alias="chunk_id")
    source_path: str = Field(default="", alias="来源路径")
    locator: str = Field(default="", alias="来源定位")
    excerpt: str = Field(default="", alias="原文摘录")
    support_note: str = Field(default="", alias="支撑说明")


class EvaluationItem(ChineseModel):
    item_id: str = Field(alias="评测项ID")
    excel_row: int = Field(alias="Excel行号", ge=2)
    question: str = Field(alias="问题", min_length=1)
    reference_answer: str = Field(alias="参考答案", min_length=1)
    trace_status: TraceStatus = Field(alias="溯源状态")
    primary_chunk_id: str = Field(default="", alias="主chunk_id")
    gold_chunk_ids: list[str] = Field(default_factory=list, alias="黄金chunk_id列表")
    gold_evidences: list[GoldEvidence] = Field(default_factory=list, alias="黄金证据")


class JudgeResult(ChineseModel):
    correctness_score: float = Field(alias="事实正确性得分", ge=0, le=35)
    keypoint_coverage_score: float = Field(alias="关键点覆盖得分", ge=0, le=20)
    groundedness_score: float = Field(alias="证据忠实性得分", ge=0, le=20)
    relevance_clarity_score: float = Field(alias="相关性与表达得分", ge=0, le=10)
    reference_keypoints: list[str] = Field(alias="参考答案关键点")
    covered_keypoints: list[str] = Field(alias="已覆盖关键点")
    missing_keypoints: list[str] = Field(alias="缺失关键点")
    unsupported_claims: list[str] = Field(alias="无依据表述")
    error_tags: list[str] = Field(alias="错误标签")
    reason: str = Field(alias="扣分原因", min_length=1)
    improvement_suggestion: str = Field(alias="改进建议", min_length=1)

    @field_validator("error_tags")
    @classmethod
    def validate_error_tags(cls, values: list[str]) -> list[str]:
        return _validated_error_tags(values)


class DeterministicScores(ChineseModel):
    retrieval_score: float = Field(alias="检索规则得分", ge=0, le=10)
    citation_score: float = Field(alias="引用规则得分", ge=0, le=5)
    total: float = Field(alias="引用及黄金来源命中得分", ge=0, le=15)
    rule_name: str = Field(alias="规则名称")
    primary_chunk_hit: bool = Field(alias="主chunk命中")
    gold_chunk_recall: float = Field(alias="黄金chunk召回率", ge=0, le=1)
    retrieved_chunk_ids: list[str] = Field(alias="检索chunk_id列表")


class EvaluationResult(ChineseModel):
    item_id: str = Field(alias="评测项ID")
    excel_row: int = Field(alias="Excel行号")
    question: str = Field(alias="问题")
    reference_answer: str = Field(alias="参考答案")
    trace_status: TraceStatus = Field(alias="溯源状态")
    answer: str = Field(default="", alias="智能体回答")
    answer_response: dict = Field(default_factory=dict, alias="问答原始结果")
    duration_seconds: float = Field(default=0, alias="耗时（秒）", ge=0)
    deterministic_scores: DeterministicScores | None = Field(
        default=None,
        alias="确定性指标",
    )
    judge_result: JudgeResult | None = Field(default=None, alias="裁判结果")
    total_score: float | None = Field(default=None, alias="总分", ge=0, le=100)
    grade: EvaluationGrade = Field(alias="评测等级")
    status: Literal["已完成", "问答失败", "评测失败"] = Field(alias="评测状态")
    error_tags: list[str] = Field(default_factory=list, alias="错误标签")
    error_summary: str = Field(default="", alias="错误摘要")

    @field_validator("error_tags")
    @classmethod
    def validate_error_tags(cls, values: list[str]) -> list[str]:
        return _validated_error_tags(values)
