from dataclasses import FrozenInstanceError

import pytest

from xhbx_rag.config import ConfigError
from xhbx_rag.evaluation.config import load_evaluation_config
from xhbx_rag.evaluation.models import (
    DeterministicScores,
    EvaluationItem,
    EvaluationResult,
    GoldEvidence,
    JudgeResult,
)
from xhbx_rag.evaluation.serialization import dump_chinese


def _judge_result(**overrides: object) -> JudgeResult:
    values: dict[str, object] = {
        "correctness_score": 30,
        "keypoint_coverage_score": 18,
        "groundedness_score": 17,
        "relevance_clarity_score": 9,
        "reference_keypoints": ["先确认客户预算"],
        "covered_keypoints": ["先确认客户预算"],
        "missing_keypoints": [],
        "unsupported_claims": [],
        "error_tags": [],
        "reason": "回答与参考答案一致，且有证据支持。",
        "improvement_suggestion": "可以补充后续行动步骤。",
    }
    values.update(overrides)
    return JudgeResult(**values)


def test_judge_result_serializes_only_chinese_business_fields() -> None:
    payload = dump_chinese(_judge_result())

    assert payload["事实正确性得分"] == 30
    assert payload["扣分原因"] == "回答与参考答案一致，且有证据支持。"
    assert "correctness_score" not in payload


def test_judge_result_rejects_out_of_range_score() -> None:
    with pytest.raises(ValueError):
        _judge_result(correctness_score=36)


def test_judge_result_rejects_unknown_error_tag() -> None:
    with pytest.raises(ValueError, match="不支持的错误标签: 其他错误"):
        _judge_result(error_tags=["其他错误"])


def test_evaluation_models_accept_chinese_aliases_and_forbid_extra_fields() -> None:
    item = EvaluationItem.model_validate(
        {
            "评测项ID": "item-1",
            "Excel行号": 2,
            "问题": "客户预算不足时应如何沟通？",
            "参考答案": "先确认客户的真实预算。",
            "溯源状态": "完整支持",
            "主chunk_id": "chunk-1",
            "黄金chunk_id列表": ["chunk-1"],
            "黄金证据": [
                {
                    "chunk_id": "chunk-1",
                    "来源路径": "课程/预算沟通.md",
                    "来源定位": "第 2 节",
                    "原文摘录": "先确认客户预算。",
                    "支撑说明": "直接支撑参考答案。",
                }
            ],
        }
    )

    assert item.item_id == "item-1"
    assert item.gold_evidences[0].source_path == "课程/预算沟通.md"
    with pytest.raises(ValueError):
        GoldEvidence(chunk_id="chunk-1", unknown_field="不允许")


def test_evaluation_item_rejects_invalid_excel_row() -> None:
    with pytest.raises(ValueError):
        EvaluationItem(
            item_id="item-1",
            excel_row=1,
            question="问题",
            reference_answer="答案",
            trace_status="未定位",
        )


def test_dump_chinese_recursively_maps_answer_response_keys() -> None:
    result = EvaluationResult(
        item_id="item-1",
        excel_row=2,
        question="问题",
        reference_answer="参考答案",
        trace_status="完整支持",
        answer_response={
            "original_query": "原问题",
            "rewritten_query": "改写后问题",
            "intent": "咨询",
            "filters": {"selected": True},
            "answer": "回答",
            "reasoning": "理由",
            "citations": [
                {
                    "evidence_index": 1,
                    "chunk_id": "chunk-1",
                    "source_path": "课程.md",
                    "locator": "第 1 节",
                    "source_excerpt": "原文",
                    "quote": "引文",
                    "display_location": "展示位置",
                    "display_excerpt": "展示原文",
                    "can_reveal": True,
                }
            ],
            "evidence_count": 1,
            "retrieval_evidences": [
                {
                    "chunk_type": "course",
                    "text": "证据",
                    "metadata": {"selected": True},
                    "score": 0.9,
                }
            ],
        },
        deterministic_scores=DeterministicScores(
            retrieval_score=10,
            citation_score=5,
            total=15,
            rule_name="主chunk命中",
            primary_chunk_hit=True,
            gold_chunk_recall=1,
            retrieved_chunk_ids=["chunk-1"],
        ),
        judge_result=_judge_result(),
        total_score=89,
        grade="优秀",
        status="已完成",
    )

    payload = dump_chinese(result)
    answer_response = payload["问答原始结果"]
    citation = answer_response["引用"][0]
    retrieval_evidence = answer_response["检索证据"][0]

    assert answer_response["原始问题"] == "原问题"
    assert answer_response["过滤条件"] == {"模型选中": True}
    assert citation["证据序号"] == 1
    assert citation["chunk_id"] == "chunk-1"
    assert citation["可查看源文件"] is True
    assert retrieval_evidence["chunk类型"] == "course"
    assert retrieval_evidence["元数据"] == {"模型选中": True}
    assert "original_query" not in answer_response


@pytest.mark.parametrize(
    "forbidden_key",
    ["correctness_score", "passed", "failed", "unsupported_claims"],
)
def test_dump_chinese_rejects_recursive_english_business_fields(
    forbidden_key: str,
) -> None:
    result = EvaluationResult(
        item_id="item-1",
        excel_row=2,
        question="问题",
        reference_answer="参考答案",
        trace_status="未定位",
        answer_response={"nested": [{forbidden_key: "不应对外输出"}]},
        grade="评测失败",
        status="评测失败",
    )

    with pytest.raises(ValueError, match="对外结果包含英文业务字段"):
        dump_chinese(result)


def test_evaluation_config_falls_back_to_same_model_and_marks_it() -> None:
    config = load_evaluation_config(
        {
            "BASE_URL": "https://example.com/v1",
            "API_KEY": "answer-key",
            "MODEL_NAME": "answer-model",
        },
        env_file=None,
    )

    assert config.judge_model_name == "answer-model"
    assert config.judge_timeout == 180
    assert config.judge_retry_attempts == 2
    assert config.same_model_judge is True


def test_evaluation_config_reads_judge_config_from_env_file(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "BASE_URL=https://answer.example.com/v1",
                "API_KEY=answer-key",
                "MODEL_NAME=answer-model",
                "EVAL_BASE_URL=https://judge.example.com/v1/",
                "EVAL_API_KEY=judge-key",
                "EVAL_MODEL_NAME=judge-model",
            ]
        ),
        encoding="utf-8",
    )

    config = load_evaluation_config(env={}, env_file=env_file)

    assert config.judge_base_url == "https://judge.example.com/v1"
    assert config.judge_model_name == "judge-model"
    assert config.same_model_judge is False


def test_evaluation_config_uses_complete_independent_judge_config() -> None:
    config = load_evaluation_config(
        {
            "BASE_URL": "https://answer.example.com/v1/",
            "API_KEY": "answer-key",
            "MODEL_NAME": "answer-model",
            "EVAL_BASE_URL": "https://judge.example.com/v1/",
            "EVAL_API_KEY": "judge-secret",
            "EVAL_MODEL_NAME": "judge-model",
            "EVAL_TIMEOUT": "45.5",
            "EVAL_RETRY_ATTEMPTS": "0",
        },
        env_file=None,
    )

    assert config.judge_base_url == "https://judge.example.com/v1"
    assert config.judge_api_key == "judge-secret"
    assert config.judge_model_name == "judge-model"
    assert config.judge_timeout == 45.5
    assert config.judge_retry_attempts == 0
    assert config.same_model_judge is False


def test_evaluation_config_marks_explicit_same_model_after_url_normalization() -> None:
    config = load_evaluation_config(
        {
            "BASE_URL": "https://example.com/v1/",
            "API_KEY": "answer-key",
            "MODEL_NAME": "answer-model",
            "EVAL_BASE_URL": "https://example.com/v1",
            "EVAL_API_KEY": "judge-key",
            "EVAL_MODEL_NAME": "answer-model",
        },
        env_file=None,
    )

    assert config.same_model_judge is True


def test_evaluation_config_is_frozen_and_hides_api_key_from_repr() -> None:
    config = load_evaluation_config(
        {
            "BASE_URL": "https://example.com/v1",
            "API_KEY": "judge-secret",
            "MODEL_NAME": "answer-model",
        },
        env_file=None,
    )

    assert "judge-secret" not in repr(config)
    with pytest.raises(FrozenInstanceError):
        config.judge_model_name = "other-model"


@pytest.mark.parametrize("missing_key", ["EVAL_BASE_URL", "EVAL_API_KEY", "EVAL_MODEL_NAME"])
def test_evaluation_config_rejects_partial_independent_config(
    missing_key: str,
) -> None:
    env = {
        "EVAL_BASE_URL": "https://judge.example.com/v1",
        "EVAL_API_KEY": "judge-key",
        "EVAL_MODEL_NAME": "judge-model",
    }
    del env[missing_key]

    with pytest.raises(
        ConfigError,
        match="EVAL_BASE_URL、EVAL_API_KEY、EVAL_MODEL_NAME 必须同时配置",
    ):
        load_evaluation_config(env, env_file=None)


def test_evaluation_config_requires_complete_fallback_config() -> None:
    with pytest.raises(ConfigError, match="缺少裁判模型配置: API_KEY, MODEL_NAME"):
        load_evaluation_config(
            {"BASE_URL": "https://example.com/v1"},
            env_file=None,
        )


@pytest.mark.parametrize("base_url", ["example.com/v1", "ftp://example.com/v1", "https:///v1"])
def test_evaluation_config_rejects_invalid_judge_url(base_url: str) -> None:
    with pytest.raises(ConfigError):
        load_evaluation_config(
            {
                "BASE_URL": base_url,
                "API_KEY": "answer-key",
                "MODEL_NAME": "answer-model",
            },
            env_file=None,
        )


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf", "invalid"])
def test_evaluation_config_rejects_invalid_timeout(timeout: str) -> None:
    with pytest.raises(ConfigError, match="EVAL_TIMEOUT 必须是有限正数"):
        load_evaluation_config(
            {
                "BASE_URL": "https://example.com/v1",
                "API_KEY": "answer-key",
                "MODEL_NAME": "answer-model",
                "EVAL_TIMEOUT": timeout,
            },
            env_file=None,
        )


@pytest.mark.parametrize("attempts", ["-1", "11", "1.5", "True", "invalid"])
def test_evaluation_config_rejects_invalid_retry_attempts(attempts: str) -> None:
    with pytest.raises(
        ConfigError,
        match="EVAL_RETRY_ATTEMPTS 必须是 0 到 10 之间的整数",
    ):
        load_evaluation_config(
            {
                "BASE_URL": "https://example.com/v1",
                "API_KEY": "answer-key",
                "MODEL_NAME": "answer-model",
                "EVAL_RETRY_ATTEMPTS": attempts,
            },
            env_file=None,
        )
