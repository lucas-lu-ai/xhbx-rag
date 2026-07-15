from __future__ import annotations

import json
import inspect
import threading
import time
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from pymilvus.client.types import Status
from pymilvus.exceptions import ConnectError, MilvusException, ParamError

from xhbx_rag.config import ConfigError, RetrievalConfig
from xhbx_rag.evaluation.judge import JudgeEvaluationError
from xhbx_rag.evaluation.models import EvaluationItem, JudgeResult
import xhbx_rag.evaluation.runner as runner_module
from xhbx_rag.evaluation.runner import (
    EvaluationPreflightError,
    compute_run_fingerprint,
    load_checkpoint_results,
    main,
    preflight_docker_milvus,
    run_items,
    run_one_item,
    validate_resume,
    write_run_metadata,
)
from xhbx_rag.evaluation.serialization import (
    dump_chinese,
    load_chinese_result,
)


def _retrieval_config(**overrides: object) -> RetrievalConfig:
    values: dict[str, object] = {
        "api_key": "answer-secret",
        "base_url": "https://answer.example/v1",
        "model_name": "answer-model",
        "vision_model_name": "",
        "embedding_base_url": "https://embedding.example/v1",
        "embedding_model_name": "embedding-model",
        "embedding_api_key": "embedding-secret",
        "rerank_base_url": "https://rerank.example/v1",
        "rerank_model_name": "rerank-model",
        "rerank_api_key": "rerank-secret",
        "milvus_mode": "docker",
        "milvus_lite_path": Path(".local/test.db"),
        "milvus_uri": "http://localhost:19530",
        "milvus_token": "",
        "milvus_collection": "case",
        "milvus_course_collection": "course",
        "milvus_vector_dim": 1024,
    }
    values.update(overrides)
    return RetrievalConfig(**values)  # type: ignore[arg-type]


def _item(index: int = 2) -> EvaluationItem:
    return EvaluationItem(
        item_id=f"row-{index}",
        excel_row=index,
        question=f"问题 {index}",
        reference_answer="先确认客户预算。",
        trace_status="完整支持",
        primary_chunk_id="c1",
        gold_chunk_ids=["c1"],
    )


def _answer_response(index: int = 2) -> dict[str, Any]:
    return {
        "original_query": f"问题 {index}",
        "rewritten_query": f"改写问题 {index}",
        "answer": "先确认客户预算。",
        "retrieval_evidences": [
            {
                "chunk_id": "c1",
                "chunk_type": "strategy",
                "text": "先确认客户预算。",
                "metadata": {"strategy_name": "预算确认"},
            }
        ],
        "citations": [
            {
                "evidence_index": 1,
                "chunk_id": "c1",
                "source_path": "case/a.md",
                "locator": {"line_start": 1},
            }
        ],
    }


def _judge_result() -> JudgeResult:
    return JudgeResult(
        correctness_score=30,
        keypoint_coverage_score=18,
        groundedness_score=17,
        relevance_clarity_score=9,
        reference_keypoints=["确认预算"],
        covered_keypoints=["确认预算"],
        missing_keypoints=[],
        unsupported_claims=[],
        error_tags=[],
        reason="回答正确且有证据支持。",
        improvement_suggestion="可以补充下一步行动。",
    )


class _Judge:
    def __init__(self, outcomes: list[object] | None = None) -> None:
        self.outcomes = list(outcomes or [_judge_result()])
        self.calls = 0

    def evaluate(
        self,
        item: EvaluationItem,
        answer_response: dict[str, Any],
    ) -> JudgeResult:
        del item, answer_response
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, JudgeResult)
        return outcome


class _Answer:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0
        self.kwargs: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> dict[str, Any]:
        self.calls += 1
        self.kwargs.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, dict)
        return outcome


class _ReadOnlyMilvusClient:
    def __init__(self, counts: dict[str, object]) -> None:
        self.counts = counts
        self.calls: list[tuple[str, str | None]] = []

    def has_collection(self, *, collection_name: str) -> bool:
        self.calls.append(("has_collection", collection_name))
        return collection_name in self.counts

    def get_collection_stats(self, *, collection_name: str) -> dict[str, object]:
        self.calls.append(("get_collection_stats", collection_name))
        return {"row_count": self.counts[collection_name]}

    def close(self) -> None:
        self.calls.append(("close", None))


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/v1")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=request,
        response=response,
    )


def test_preflight_rejects_lite_mode_without_constructing_client() -> None:
    constructed = False

    def factory(**kwargs: object) -> _ReadOnlyMilvusClient:
        nonlocal constructed
        constructed = True
        raise AssertionError(kwargs)

    with pytest.raises(EvaluationPreflightError, match="只允许使用 Docker Milvus"):
        preflight_docker_milvus(
            replace(_retrieval_config(), milvus_mode="lite"),
            client_factory=factory,
        )

    assert constructed is False


def test_preflight_rejects_non_local_docker_uri_without_constructing_client() -> None:
    constructed = False

    def factory(**kwargs: object) -> _ReadOnlyMilvusClient:
        nonlocal constructed
        constructed = True
        raise AssertionError(kwargs)

    with pytest.raises(
        EvaluationPreflightError,
        match="http://localhost:19530",
    ):
        preflight_docker_milvus(
            _retrieval_config(milvus_uri="http://milvus.example:19530"),
            client_factory=factory,
        )

    assert constructed is False


def test_preflight_is_read_only_omits_empty_token_and_returns_chinese_stats() -> None:
    client = _ReadOnlyMilvusClient({"case": 3, "course": 5})
    received_kwargs: dict[str, object] = {}

    def factory(**kwargs: object) -> _ReadOnlyMilvusClient:
        received_kwargs.update(kwargs)
        return client

    stats = preflight_docker_milvus(
        _retrieval_config(milvus_token=""),
        client_factory=factory,
    )

    assert received_kwargs == {"uri": "http://localhost:19530"}
    assert stats == {
        "case": {"存在": True, "数据量": 3},
        "course": {"存在": True, "数据量": 5},
    }
    assert client.calls == [
        ("has_collection", "case"),
        ("get_collection_stats", "case"),
        ("has_collection", "course"),
        ("get_collection_stats", "course"),
        ("close", None),
    ]


def test_preflight_passes_non_empty_token() -> None:
    received_kwargs: dict[str, object] = {}

    def factory(**kwargs: object) -> _ReadOnlyMilvusClient:
        received_kwargs.update(kwargs)
        return _ReadOnlyMilvusClient({"case": 1, "course": 0})

    preflight_docker_milvus(
        _retrieval_config(milvus_token="milvus-secret"),
        client_factory=factory,
    )

    assert received_kwargs == {
        "uri": "http://localhost:19530",
        "token": "milvus-secret",
    }


def test_preflight_rejects_all_missing_or_empty_collections() -> None:
    client = _ReadOnlyMilvusClient({"case": 0})

    with pytest.raises(EvaluationPreflightError, match="目标 collection 均为空"):
        preflight_docker_milvus(
            _retrieval_config(),
            client_factory=lambda **_kwargs: client,
        )

    assert client.calls[-1] == ("close", None)


@pytest.mark.parametrize("row_count", [True, -1, 1.5, "不是数字"])
def test_preflight_rejects_invalid_row_count_in_chinese(row_count: object) -> None:
    with pytest.raises(EvaluationPreflightError, match="数据量无效") as captured:
        preflight_docker_milvus(
            _retrieval_config(),
            client_factory=lambda **_kwargs: _ReadOnlyMilvusClient(
                {"case": row_count, "course": 1}
            ),
        )

    assert "answer-secret" not in str(captured.value)


def test_preflight_hides_connection_error_and_token() -> None:
    token = "do-not-leak-token"

    def factory(**kwargs: object) -> _ReadOnlyMilvusClient:
        raise RuntimeError(f"连接失败: {kwargs['token']}")

    with pytest.raises(
        EvaluationPreflightError,
        match="连接或读取统计失败",
    ) as captured:
        preflight_docker_milvus(
            _retrieval_config(milvus_token=token),
            client_factory=factory,
        )

    assert token not in str(captured.value)


def test_preflight_close_is_best_effort() -> None:
    class CloseFailingClient(_ReadOnlyMilvusClient):
        def close(self) -> None:
            super().close()
            raise RuntimeError("关闭失败")

    stats = preflight_docker_milvus(
        _retrieval_config(),
        client_factory=lambda **_kwargs: CloseFailingClient(
            {"case": 1, "course": 0}
        ),
    )

    assert stats["case"]["数据量"] == 1


def test_answer_transient_failure_retries_then_succeeds_with_exact_kwargs(
    tmp_path: Path,
) -> None:
    answer = _Answer([httpx.ReadTimeout("timeout"), _answer_response()])
    sleeps: list[float] = []

    result = run_one_item(
        _item(),
        answer_fn=answer,
        judge=_Judge(),
        top_n=20,
        top_k=5,
        collections=["case", "course"],
        project_root=tmp_path,
        chunk_catalog={"c1"},
        max_attempts=3,
        sleep_fn=sleeps.append,
    )

    assert answer.calls == 2
    assert answer.kwargs == [
        {
            "query": "问题 2",
            "top_n": 20,
            "top_k": 5,
            "collections": ["case", "course"],
            "project_root": tmp_path,
        },
        {
            "query": "问题 2",
            "top_n": 20,
            "top_k": 5,
            "collections": ["case", "course"],
            "project_root": tmp_path,
        },
    ]
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 2
    assert result.status == "已完成"


@pytest.mark.parametrize(
    "error",
    [ConfigError("配置错误"), ValueError("输入错误"), _http_status_error(400)],
)
def test_answer_non_retryable_failure_is_not_retried(error: Exception) -> None:
    answer = _Answer([error])

    result = run_one_item(
        _item(),
        answer_fn=answer,
        judge=_Judge(),
        chunk_catalog={"c1"},
        max_attempts=3,
        sleep_fn=lambda _delay: pytest.fail("不可重试错误不应退避"),
    )

    assert answer.calls == 1
    assert result.total_score == 0
    assert result.grade == "问答失败"
    assert result.status == "问答失败"
    assert result.error_tags == ["问答执行失败"]
    assert result.error_summary == "问答执行失败，请稍后重试"
    assert str(error) not in result.error_summary


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_answer_retryable_http_status_retries(status_code: int) -> None:
    answer = _Answer([_http_status_error(status_code), _answer_response()])

    result = run_one_item(
        _item(),
        answer_fn=answer,
        judge=_Judge(),
        chunk_catalog={"c1"},
        max_attempts=2,
        sleep_fn=lambda _delay: None,
    )

    assert answer.calls == 2
    assert result.status == "已完成"


def test_answer_final_failure_is_fixed_safe_chinese_zero() -> None:
    secret = "answer-key-must-not-leak"
    answer = _Answer([httpx.ReadTimeout(secret)] * 3)

    result = run_one_item(
        _item(),
        answer_fn=answer,
        judge=_Judge(),
        chunk_catalog={"c1"},
        max_attempts=3,
        sleep_fn=lambda _delay: None,
    )

    assert answer.calls == 3
    assert result.total_score == 0
    assert result.grade == "问答失败"
    assert result.error_tags == ["问答执行失败"]
    assert result.error_summary == "问答执行失败，请稍后重试"
    assert secret not in json.dumps(dump_chinese(result), ensure_ascii=False)


def test_temporary_milvus_connection_failure_retries() -> None:
    answer = _Answer(
        [
            ConnectError(message="failed to connect to Milvus"),
            _answer_response(),
        ]
    )

    result = run_one_item(
        _item(),
        answer_fn=answer,
        judge=_Judge(),
        chunk_catalog={"c1"},
        max_attempts=2,
        sleep_fn=lambda _delay: None,
    )

    assert answer.calls == 2
    assert result.status == "已完成"


def test_base_milvus_connect_failed_status_retries() -> None:
    answer = _Answer(
        [
            MilvusException(
                code=Status.CONNECT_FAILED,
                message="failed to connect to Milvus",
            ),
            _answer_response(),
        ]
    )

    result = run_one_item(
        _item(),
        answer_fn=answer,
        judge=_Judge(),
        chunk_catalog={"c1"},
        max_attempts=2,
        sleep_fn=lambda _delay: None,
    )

    assert answer.calls == 2
    assert result.status == "已完成"


def test_non_connection_milvus_failure_does_not_retry() -> None:
    answer = _Answer([ParamError(message="字段参数非法")])

    result = run_one_item(
        _item(),
        answer_fn=answer,
        judge=_Judge(),
        chunk_catalog={"c1"},
        max_attempts=3,
        sleep_fn=lambda _delay: pytest.fail("非连接异常不应重试"),
    )

    assert answer.calls == 1
    assert result.status == "问答失败"


def test_judge_transport_failure_retries_then_succeeds() -> None:
    judge = _Judge([httpx.ConnectError("断开"), _judge_result()])

    result = run_one_item(
        _item(),
        answer_fn=_Answer([_answer_response()]),
        judge=judge,
        chunk_catalog={"c1"},
        max_attempts=2,
        sleep_fn=lambda _delay: None,
    )

    assert judge.calls == 2
    assert result.status == "已完成"


def test_judge_format_failure_is_not_retried_and_keeps_real_rules() -> None:
    judge = _Judge([JudgeEvaluationError("格式失败 secret-key")])

    result = run_one_item(
        _item(),
        answer_fn=_Answer([_answer_response()]),
        judge=judge,
        chunk_catalog={"c1"},
        max_attempts=3,
        sleep_fn=lambda _delay: pytest.fail("格式失败不应外层重试"),
    )

    assert judge.calls == 1
    assert result.deterministic_scores is not None
    assert result.total_score is None
    assert result.grade == "评测失败"
    assert result.status == "评测失败"
    assert result.error_tags == ["裁判执行失败"]
    assert result.error_summary == "裁判执行失败，请稍后重试"
    assert "secret-key" not in json.dumps(dump_chinese(result), ensure_ascii=False)


def test_judge_final_transport_failure_uses_safe_summary() -> None:
    secret = "judge-token-must-not-leak"
    judge = _Judge([httpx.ConnectError(secret)] * 2)

    result = run_one_item(
        _item(),
        answer_fn=_Answer([_answer_response()]),
        judge=judge,
        chunk_catalog={"c1"},
        max_attempts=2,
        sleep_fn=lambda _delay: None,
    )

    assert judge.calls == 2
    assert result.deterministic_scores is not None
    assert result.total_score is None
    assert result.error_summary == "裁判执行失败，请稍后重试"
    assert secret not in json.dumps(dump_chinese(result), ensure_ascii=False)


def test_default_answer_function_is_local_service() -> None:
    from xhbx_rag.web.services import answer_question as local_answer_question

    assert (
        inspect.signature(run_one_item).parameters["answer_fn"].default
        is local_answer_question
    )
    assert (
        inspect.signature(run_items).parameters["answer_fn"].default
        is local_answer_question
    )


@pytest.mark.parametrize("max_attempts", [True, False, 0, -1, 1.5, "2"])
def test_run_one_item_requires_strict_positive_integer_attempts(
    max_attempts: object,
) -> None:
    with pytest.raises(ValueError, match="max_attempts 必须是正整数"):
        run_one_item(
            _item(),
            answer_fn=_Answer([_answer_response()]),
            judge=_Judge(),
            max_attempts=max_attempts,  # type: ignore[arg-type]
        )


def _fingerprint(**overrides: object) -> str:
    values: dict[str, object] = {
        "input_sha256": "a" * 64,
        "scoring_version": "v1",
        "top_n": 20,
        "top_k": 5,
        "answer_model_name": "answer-model",
        "judge_model_name": "judge-model",
        "same_model_judge": False,
        "milvus_uri": "http://localhost:19530",
        "collection_stats": {
            "case": {"存在": True, "数据量": 3},
            "course": {"存在": True, "数据量": 5},
        },
    }
    values.update(overrides)
    return compute_run_fingerprint(**values)  # type: ignore[arg-type]


def test_fingerprint_is_order_stable_and_each_fixed_field_changes_it() -> None:
    expected = _fingerprint()
    reversed_stats = {
        "course": {"数据量": 5, "存在": True},
        "case": {"数据量": 3, "存在": True},
    }
    assert _fingerprint(collection_stats=reversed_stats) == expected

    variants = [
        {"input_sha256": "b" * 64},
        {"scoring_version": "v2"},
        {"top_n": 19},
        {"top_k": 4},
        {"answer_model_name": "answer-model-2"},
        {"judge_model_name": "judge-model-2"},
        {"same_model_judge": True},
        {
            "collection_stats": {
                "case": {"存在": True, "数据量": 4},
                "course": {"存在": True, "数据量": 5},
            }
        },
        {
            "collection_stats": {
                "renamed-case": {"存在": True, "数据量": 3},
                "course": {"存在": True, "数据量": 5},
            }
        },
        {
            "collection_stats": {
                "case": {"存在": False, "数据量": 3},
                "course": {"存在": True, "数据量": 5},
            }
        },
    ]
    assert all(_fingerprint(**variant) != expected for variant in variants)


def test_fingerprint_normalizes_sha_text_fields_collection_names_and_uri() -> None:
    normalized = _fingerprint()

    variant = _fingerprint(
        input_sha256="A" * 64,
        scoring_version="  v1  ",
        answer_model_name="  answer-model  ",
        judge_model_name="  judge-model  ",
        milvus_uri="  http://localhost:19530/  ",
        collection_stats={
            " case ": {"存在": True, "数据量": 3},
            " course ": {"存在": True, "数据量": 5},
        },
    )

    assert variant == normalized


@pytest.mark.parametrize(
    "overrides",
    [
        {"input_sha256": "a" * 63},
        {"input_sha256": "g" * 64},
        {"input_sha256": 123},
        {"scoring_version": "  "},
        {"scoring_version": 1},
        {"answer_model_name": ""},
        {"answer_model_name": None},
        {"judge_model_name": "\t"},
        {"judge_model_name": False},
        {"top_n": True},
        {"top_n": 0},
        {"top_n": 101},
        {"top_n": 20.0},
        {"top_k": False},
        {"top_k": 0},
        {"top_k": 21},
        {"top_k": 5.0},
        {"top_n": 4, "top_k": 5},
        {"same_model_judge": 1},
        {"same_model_judge": "否"},
        {"milvus_uri": "http://milvus.example:19530"},
        {"milvus_uri": 19530},
    ],
)
def test_fingerprint_rejects_invalid_scalar_fields(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="运行指纹配置无效"):
        _fingerprint(**overrides)


@pytest.mark.parametrize(
    "collection_stats",
    [
        {},
        [],
        {"": {"存在": True, "数据量": 1}},
        {"   ": {"存在": True, "数据量": 1}},
        {1: {"存在": True, "数据量": 1}},
        {"case": []},
        {"case": {"存在": True}},
        {"case": {"数据量": 1}},
        {"case": {"存在": True, "数据量": 1, "额外字段": "非法"}},
        {"case": {"exists": True, "数据量": 1}},
        {"case": {"存在": 1, "数据量": 1}},
        {"case": {"存在": True, "数据量": True}},
        {"case": {"存在": True, "数据量": -1}},
        {"case": {"存在": True, "数据量": 1.0}},
        {"case": {"存在": True, "数据量": float("nan")}},
    ],
)
def test_fingerprint_rejects_invalid_collection_stats(
    collection_stats: object,
) -> None:
    with pytest.raises(ValueError, match="运行指纹配置无效"):
        _fingerprint(collection_stats=collection_stats)


def test_run_metadata_is_atomic_chinese_and_contains_no_secret(tmp_path: Path) -> None:
    write_run_metadata(
        tmp_path,
        fingerprint="fingerprint",
        config_payload={
            "输入SHA256": "a" * 64,
            "问答模型名": "answer-model",
            "Milvus地址": "http://localhost:19530",
        },
    )

    payload = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert payload == {
        "运行配置指纹": "fingerprint",
        "运行配置": {
            "输入SHA256": "a" * 64,
            "问答模型名": "answer-model",
            "Milvus地址": "http://localhost:19530",
        },
    }
    assert not (tmp_path / "run.json.tmp").exists()
    assert "key" not in json.dumps(payload, ensure_ascii=False).lower()
    assert "token" not in json.dumps(payload, ensure_ascii=False).lower()


def test_run_metadata_fsyncs_new_directory_parent_and_replace_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(
        runner_module,
        "_fsync_directory",
        lambda path: calls.append(Path(path)),
        raising=False,
    )
    run_dir = tmp_path / "outputs" / "run-1"

    write_run_metadata(run_dir, fingerprint="fingerprint", config_payload={})

    assert calls == [tmp_path, run_dir.parent, run_dir]


def test_run_metadata_propagates_new_directory_parent_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "outputs" / "run-1"

    def fail_directory_fsync(_path: Path) -> None:
        raise OSError("目录 fsync 失败")

    monkeypatch.setattr(
        runner_module,
        "_fsync_directory",
        fail_directory_fsync,
        raising=False,
    )

    with pytest.raises(OSError, match="目录 fsync 失败"):
        write_run_metadata(
            run_dir,
            fingerprint="fingerprint",
            config_payload={},
        )

    assert not (run_dir / "run.json").exists()


def test_run_metadata_propagates_post_replace_directory_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()

    def fail_directory_fsync(_path: Path) -> None:
        raise OSError("目录 fsync 失败")

    monkeypatch.setattr(
        runner_module,
        "_fsync_directory",
        fail_directory_fsync,
        raising=False,
    )

    with pytest.raises(OSError, match="目录 fsync 失败"):
        write_run_metadata(
            run_dir,
            fingerprint="fingerprint",
            config_payload={},
        )

    assert (run_dir / "run.json").exists()


def test_run_metadata_rejects_secret_fields_before_writing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="运行元数据不得包含密钥"):
        write_run_metadata(
            tmp_path,
            fingerprint="fingerprint",
            config_payload={"API_KEY": "do-not-write"},
        )

    assert not (tmp_path / "run.json").exists()


@pytest.mark.parametrize(
    "config_payload",
    [
        {"Milvus地址": "http://runner-user:runner-password@localhost:19530"},
        {"嵌套配置": ({"API_KEY": "tuple-secret"},)},
    ],
)
def test_run_metadata_rejects_url_userinfo_and_tuple_secrets(
    tmp_path: Path,
    config_payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="不得包含密钥"):
        write_run_metadata(
            tmp_path,
            fingerprint="fingerprint",
            config_payload=config_payload,
        )

    assert not (tmp_path / "run.json").exists()


@pytest.mark.parametrize(
    "config_payload",
    [
        {"访问token值": "secret"},
        {"apiKey": "secret"},
        {"accessToken": "secret"},
        {"嵌套配置": ({"clientSecret": "secret"},)},
    ],
)
def test_run_metadata_rejects_mixed_and_camel_case_secret_keys(
    tmp_path: Path,
    config_payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="不得包含密钥"):
        write_run_metadata(
            tmp_path,
            fingerprint="fingerprint",
            config_payload=config_payload,
        )

    assert not (tmp_path / "run.json").exists()


@pytest.mark.parametrize(
    "safe_key",
    ["monkey备注", "hockey得分", "tokenization方式"],
)
def test_run_metadata_secret_scan_avoids_substring_false_positives(
    tmp_path: Path,
    safe_key: str,
) -> None:
    write_run_metadata(
        tmp_path,
        fingerprint="fingerprint",
        config_payload={safe_key: "普通值"},
    )

    payload = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert payload["运行配置"][safe_key] == "普通值"


@pytest.mark.parametrize(
    "config_payload",
    [
        {"answer_model_name": "answer-model"},
        {"嵌套配置": ({"answer_model_name": "answer-model"},)},
    ],
)
def test_run_metadata_rejects_english_business_fields(
    tmp_path: Path,
    config_payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="英文业务字段"):
        write_run_metadata(
            tmp_path,
            fingerprint="fingerprint",
            config_payload=config_payload,
        )

    assert not (tmp_path / "run.json").exists()


def test_resume_rejects_mismatched_fingerprint_before_checkpoint(
    tmp_path: Path,
) -> None:
    write_run_metadata(tmp_path, fingerprint="old", config_payload={})
    (tmp_path / "results.jsonl").write_text("不是 JSON", encoding="utf-8")

    with pytest.raises(ValueError, match="运行配置指纹不一致"):
        validate_resume(tmp_path, expected_fingerprint="new")


def test_chinese_result_roundtrip_restores_nested_answer_response() -> None:
    result = run_one_item(
        _item(),
        answer_fn=_Answer([_answer_response()]),
        judge=_Judge(),
        chunk_catalog={"c1"},
    )

    restored = load_chinese_result(
        json.loads(json.dumps(dump_chinese(result), ensure_ascii=False))
    )

    assert restored == result
    assert restored.answer_response == _answer_response()


def test_load_chinese_result_rejects_unknown_english_business_key() -> None:
    result = run_one_item(
        _item(),
        answer_fn=_Answer([_answer_response()]),
        judge=_Judge(),
        chunk_catalog={"c1"},
    )
    payload = dump_chinese(result)
    payload["问答原始结果"]["unknown_business_key"] = "非法"

    with pytest.raises(ValueError, match="英文业务字段"):
        load_chinese_result(payload)


def test_load_chinese_result_rejects_top_level_internal_english_key() -> None:
    result = run_one_item(
        _item(),
        answer_fn=_Answer([_answer_response()]),
        judge=_Judge(),
        chunk_catalog={"c1"},
    )
    payload = dump_chinese(result)
    payload["item_id"] = payload.pop("评测项ID")

    with pytest.raises(ValueError, match="英文业务字段"):
        load_chinese_result(payload)


class _ConditionalJudge:
    def __init__(self, failing_ids: set[str] | None = None) -> None:
        self.failing_ids = failing_ids or set()
        self.calls: list[str] = []

    def evaluate(
        self,
        item: EvaluationItem,
        answer_response: dict[str, Any],
    ) -> JudgeResult:
        del answer_response
        self.calls.append(item.item_id)
        if item.item_id in self.failing_ids:
            raise JudgeEvaluationError("裁判格式失败")
        return _judge_result()


class _InlineExecutor:
    instances: list["_InlineExecutor"] = []

    def __init__(self, max_workers: int) -> None:
        self.max_workers = max_workers
        self.submitted: list[Callable[..., object]] = []
        self.instances.append(self)

    def __enter__(self) -> "_InlineExecutor":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def submit(
        self,
        fn: Callable[..., object],
        *args: object,
        **kwargs: object,
    ) -> Future[object]:
        self.submitted.append(fn)
        future: Future[object] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:
            future.set_exception(exc)
        return future


def test_checkpoint_writer_fsyncs_parent_when_results_file_is_created(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(
        runner_module,
        "_fsync_directory",
        lambda path: calls.append(Path(path)),
        raising=False,
    )
    results_path = tmp_path / "run" / "results.jsonl"
    results_path.parent.mkdir()

    with runner_module._CheckpointWriter(results_path):
        pass

    assert calls == [results_path.parent]


def test_checkpoint_writer_propagates_results_parent_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results_path = tmp_path / "run" / "results.jsonl"
    results_path.parent.mkdir()

    def fail_directory_fsync(_path: Path) -> None:
        raise OSError("目录 fsync 失败")

    monkeypatch.setattr(
        runner_module,
        "_fsync_directory",
        fail_directory_fsync,
        raising=False,
    )

    with pytest.raises(OSError, match="目录 fsync 失败"):
        with runner_module._CheckpointWriter(results_path):
            pass


def test_directory_fsync_closes_fd_and_propagates_os_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []
    monkeypatch.setattr(runner_module.os, "open", lambda *_args: 123)
    monkeypatch.setattr(
        runner_module.os,
        "fsync",
        lambda _fd: (_ for _ in ()).throw(OSError("目录 fsync 失败")),
    )
    monkeypatch.setattr(runner_module.os, "close", closed.append)

    with pytest.raises(OSError, match="目录 fsync 失败"):
        runner_module._fsync_directory(tmp_path)

    assert closed == [123]


def _assert_no_unknown_english_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.isascii() and any(
                character.isalpha() for character in key
            ):
                assert key == "chunk_id"
            _assert_no_unknown_english_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_unknown_english_keys(item)


def test_run_items_uses_separate_answer_and_judge_executors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _InlineExecutor.instances = []
    monkeypatch.setattr(runner_module, "ThreadPoolExecutor", _InlineExecutor)

    results = run_items(
        [_item(2), _item(3), _item(4)],
        judge=_ConditionalJudge(),
        run_dir=tmp_path,
        fingerprint="fingerprint",
        answer_fn=lambda **_kwargs: _answer_response(),
        chunk_catalog={"c1"},
        concurrency=3,
        judge_concurrency=2,
        max_attempts=1,
    )

    assert [executor.max_workers for executor in _InlineExecutor.instances] == [3, 2]
    assert len(_InlineExecutor.instances[0].submitted) == 3
    assert len(_InlineExecutor.instances[1].submitted) == 3
    assert [result.excel_row for result in results] == [2, 3, 4]


def test_run_items_appends_one_chinese_fsynced_terminal_line_per_item(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fsync_calls: list[int] = []
    real_fsync = runner_module.os.fsync

    def tracking_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(runner_module.os, "fsync", tracking_fsync)
    results = run_items(
        [_item(3), _item(2)],
        judge=_ConditionalJudge(),
        run_dir=tmp_path,
        fingerprint="fingerprint",
        answer_fn=lambda **_kwargs: _answer_response(),
        chunk_catalog={"c1"},
        concurrency=2,
        judge_concurrency=2,
        max_attempts=1,
    )

    lines = (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()
    payloads = [json.loads(line) for line in lines]
    assert len(fsync_calls) >= len(results)
    assert len(payloads) == 2
    assert {payload["评测项ID"] for payload in payloads} == {"row-2", "row-3"}
    assert all(payload["评测状态"] == "已完成" for payload in payloads)
    for payload in payloads:
        _assert_no_unknown_english_keys(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("concurrency", True, "concurrency 必须是正整数"),
        ("concurrency", 0, "concurrency 必须是正整数"),
        ("judge_concurrency", False, "judge_concurrency 必须是正整数"),
        ("judge_concurrency", -1, "judge_concurrency 必须是正整数"),
        ("max_attempts", True, "max_attempts 必须是正整数"),
        ("max_attempts", 0, "max_attempts 必须是正整数"),
    ],
)
def test_run_items_rejects_non_strict_positive_concurrency_and_attempts(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    kwargs: dict[str, object] = {
        "concurrency": 2,
        "judge_concurrency": 2,
        "max_attempts": 1,
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=message):
        run_items(
            [_item()],
            judge=_ConditionalJudge(),
            run_dir=tmp_path,
            fingerprint="fingerprint",
            answer_fn=lambda **_answer_kwargs: _answer_response(),
            **kwargs,  # type: ignore[arg-type]
        )


def test_fresh_run_rejects_existing_results_without_calling_answer(
    tmp_path: Path,
) -> None:
    (tmp_path / "results.jsonl").write_text("已有结果\n", encoding="utf-8")

    with pytest.raises(ValueError, match="已有评测结果"):
        run_items(
            [_item()],
            judge=_ConditionalJudge(),
            run_dir=tmp_path,
            fingerprint="fingerprint",
            answer_fn=lambda **_kwargs: pytest.fail("不得覆盖后继续问答"),
            max_attempts=1,
        )


def test_resume_skips_completed_and_answer_failure_but_rejudges_evaluation_failure(
    tmp_path: Path,
) -> None:
    def initial_answer(**kwargs: object) -> dict[str, Any]:
        if kwargs["query"] == "问题 3":
            raise ValueError("固定输入失败")
        return _answer_response()

    first_results = run_items(
        [_item(4), _item(2), _item(3)],
        judge=_ConditionalJudge({"row-4"}),
        run_dir=tmp_path,
        fingerprint="fingerprint",
        answer_fn=initial_answer,
        chunk_catalog={"c1"},
        max_attempts=1,
    )
    first_by_id = {result.item_id: result for result in first_results}
    assert first_by_id["row-2"].status == "已完成"
    assert first_by_id["row-3"].status == "问答失败"
    assert first_by_id["row-4"].status == "评测失败"
    assert first_by_id["row-4"].deterministic_scores is not None

    resumed_judge = _ConditionalJudge()
    resumed_results = run_items(
        [_item(2), _item(3), _item(4)],
        judge=resumed_judge,
        run_dir=tmp_path,
        fingerprint="fingerprint",
        resume=True,
        answer_fn=lambda **_kwargs: pytest.fail(
            "resume 不应重复调用已落盘问答"
        ),
        chunk_catalog={"c1"},
        max_attempts=1,
    )

    resumed_by_id = {result.item_id: result for result in resumed_results}
    assert resumed_judge.calls == ["row-4"]
    assert resumed_by_id["row-2"] == first_by_id["row-2"]
    assert resumed_by_id["row-3"] == first_by_id["row-3"]
    assert resumed_by_id["row-4"].status == "已完成"
    assert resumed_by_id["row-4"].answer_response == _answer_response()
    assert (
        resumed_by_id["row-4"].deterministic_scores
        == first_by_id["row-4"].deterministic_scores
    )

    latest = load_checkpoint_results(
        tmp_path / "results.jsonl",
        expected_item_ids={"row-2", "row-3", "row-4"},
    )
    assert latest["row-4"].status == "已完成"
    checkpoint_lines = (tmp_path / "results.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(checkpoint_lines) == 4


def test_resume_checks_fingerprint_before_parsing_checkpoint(tmp_path: Path) -> None:
    write_run_metadata(tmp_path, fingerprint="old", config_payload={})
    (tmp_path / "results.jsonl").write_text("坏 JSON", encoding="utf-8")

    with pytest.raises(ValueError, match="运行配置指纹不一致"):
        run_items(
            [_item()],
            judge=_ConditionalJudge(),
            run_dir=tmp_path,
            fingerprint="new",
            resume=True,
            answer_fn=lambda **_kwargs: pytest.fail("不得执行问答"),
        )


def test_checkpoint_duplicate_ids_are_latest_wins(tmp_path: Path) -> None:
    first = run_one_item(
        _item(),
        answer_fn=_Answer([_answer_response()]),
        judge=_Judge([JudgeEvaluationError("失败")]),
        chunk_catalog={"c1"},
        max_attempts=1,
    )
    second = run_one_item(
        _item(),
        answer_fn=_Answer([_answer_response()]),
        judge=_Judge(),
        chunk_catalog={"c1"},
        max_attempts=1,
    )
    checkpoint = tmp_path / "results.jsonl"
    checkpoint.write_text(
        "\n".join(
            json.dumps(dump_chinese(result), ensure_ascii=False)
            for result in (first, second)
        )
        + "\n",
        encoding="utf-8",
    )

    latest = load_checkpoint_results(
        checkpoint,
        expected_item_ids={"row-2"},
    )

    assert latest == {"row-2": second}


@pytest.mark.parametrize(
    "line",
    [
        "不是 JSON",
        json.dumps({"评测项ID": "row-2"}, ensure_ascii=False),
    ],
)
def test_checkpoint_rejects_bad_json_or_invalid_result(
    tmp_path: Path,
    line: str,
) -> None:
    checkpoint = tmp_path / "results.jsonl"
    checkpoint.write_text(line + "\n", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="检查点第 1 行不是合法的中文评测结果",
    ):
        load_checkpoint_results(checkpoint, expected_item_ids={"row-2"})


def test_checkpoint_rejects_item_not_in_current_dataset(tmp_path: Path) -> None:
    result = run_one_item(
        _item(99),
        answer_fn=_Answer([_answer_response(99)]),
        judge=_Judge(),
        chunk_catalog={"c1"},
        max_attempts=1,
    )
    checkpoint = tmp_path / "results.jsonl"
    checkpoint.write_text(
        json.dumps(dump_chinese(result), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="不属于当前评测集"):
        load_checkpoint_results(checkpoint, expected_item_ids={"row-2"})


def test_fifty_items_run_concurrently_return_sorted_and_checkpoint_once_each(
    tmp_path: Path,
) -> None:
    lock = threading.Lock()
    active = 0
    max_active = 0

    def concurrent_answer(**_kwargs: object) -> dict[str, Any]:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.003)
        with lock:
            active -= 1
        return _answer_response()

    items = [_item(index) for index in range(51, 1, -1)]
    results = run_items(
        items,
        judge=_ConditionalJudge(),
        run_dir=tmp_path,
        fingerprint="fingerprint",
        answer_fn=concurrent_answer,
        chunk_catalog={"c1"},
        concurrency=4,
        judge_concurrency=3,
        max_attempts=1,
    )

    payloads = [
        json.loads(line)
        for line in (tmp_path / "results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert max_active > 1
    assert [result.excel_row for result in results] == list(range(2, 52))
    assert len(payloads) == 50
    assert len({payload["评测项ID"] for payload in payloads}) == 50
    assert all(
        payload["评测状态"] in {"已完成", "问答失败", "评测失败"}
        for payload in payloads
    )


def test_preflight_module_entry_prints_chinese_json_without_answer_or_judge(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _retrieval_config()
    monkeypatch.setattr(
        runner_module.RetrievalConfig,
        "from_env",
        classmethod(lambda cls: config),
    )
    monkeypatch.setattr(
        runner_module,
        "preflight_docker_milvus",
        lambda _config: {"case": {"存在": True, "数据量": 3}},
    )
    monkeypatch.setattr(
        runner_module,
        "answer_question",
        lambda **_kwargs: pytest.fail("预检不得调用问答"),
    )
    monkeypatch.setattr(
        runner_module,
        "EvaluationJudgeAgent",
        lambda *_args, **_kwargs: pytest.fail("预检不得创建裁判"),
    )

    exit_code = main(["--preflight"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert json.loads(captured.out) == {
        "Docker Milvus预检": {"case": {"存在": True, "数据量": 3}}
    }
    assert captured.err == ""


@pytest.mark.parametrize(
    "error",
    [
        ConfigError("缺少必要环境变量"),
        ValueError("Milvus 向量维度配置无效"),
        EvaluationPreflightError("预检失败"),
    ],
)
def test_preflight_module_entry_returns_two_and_writes_chinese_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    if not isinstance(error, EvaluationPreflightError):
        monkeypatch.setattr(
            runner_module.RetrievalConfig,
            "from_env",
            classmethod(lambda cls: (_ for _ in ()).throw(error)),
        )
    else:
        monkeypatch.setattr(
            runner_module.RetrievalConfig,
            "from_env",
            classmethod(lambda cls: _retrieval_config()),
        )
        monkeypatch.setattr(
            runner_module,
            "preflight_docker_milvus",
            lambda _config: (_ for _ in ()).throw(error),
        )

    exit_code = main(["--preflight"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert str(error) in captured.err
