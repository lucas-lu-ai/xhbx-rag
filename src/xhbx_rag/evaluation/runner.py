from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from pymilvus import MilvusClient
from pymilvus.client.types import LoadState, Status
from pymilvus.exceptions import (
    ConnectError,
    ConnectionNotExistException,
    MilvusException,
    MilvusUnavailableException,
)

from xhbx_rag.config import ConfigError, RetrievalConfig
from xhbx_rag.evaluation.judge import (
    EvaluationJudgeAgent,
    JudgeEvaluationError,
)
from xhbx_rag.evaluation.metrics import aggregate_result, score_deterministic
from xhbx_rag.evaluation.models import (
    DeterministicScores,
    EvaluationItem,
    EvaluationResult,
    JudgeResult,
)
from xhbx_rag.evaluation.serialization import dump_chinese, load_chinese_result
from xhbx_rag.milvus_store import configured_collection_names
from xhbx_rag.web.services import answer_question


ANSWER_FAILURE_SUMMARY = "问答执行失败，请稍后重试"
JUDGE_FAILURE_SUMMARY = "裁判执行失败，请稍后重试"
LOCAL_DOCKER_MILVUS_URI = "http://localhost:19530"
PREFLIGHT_LOAD_TIMEOUT_SECONDS = 30.0


class EvaluationPreflightError(RuntimeError):
    """Docker Milvus 只读预检失败。"""


class _Judge(Protocol):
    def evaluate(
        self,
        item: EvaluationItem,
        answer_response: dict[str, Any],
    ) -> JudgeResult:
        """评测一条已完成的问答结果。"""


AnswerFunction = Callable[..., dict[str, Any]]
SleepFunction = Callable[[float], None]


@dataclass(frozen=True)
class _AnswerStageOutcome:
    item: EvaluationItem
    answer_response: dict[str, Any]
    duration_seconds: float
    deterministic_scores: DeterministicScores | None
    terminal_result: EvaluationResult | None = None


def preflight_docker_milvus(
    config: RetrievalConfig,
    client_factory: Callable[..., Any] = MilvusClient,
) -> dict[str, dict[str, bool | int]]:
    if config.milvus_mode != "docker":
        raise EvaluationPreflightError("评测只允许使用 Docker Milvus")
    if config.milvus_uri.rstrip("/") != LOCAL_DOCKER_MILVUS_URI:
        raise EvaluationPreflightError(
            "评测只允许连接宿主机 Docker Milvus："
            f"{LOCAL_DOCKER_MILVUS_URI}"
        )

    client: Any | None = None
    stats: dict[str, dict[str, bool | int]] = {}
    client_kwargs: dict[str, object] = {
        "uri": config.milvus_uri,
        "timeout": PREFLIGHT_LOAD_TIMEOUT_SECONDS,
    }
    if config.milvus_token:
        client_kwargs["token"] = config.milvus_token

    try:
        client = client_factory(**client_kwargs)
        for collection_name in configured_collection_names(config):
            exists = bool(
                client.has_collection(
                    collection_name=collection_name,
                    timeout=PREFLIGHT_LOAD_TIMEOUT_SECONDS,
                )
            )
            row_count = 0
            if exists:
                raw_stats = client.get_collection_stats(
                    collection_name=collection_name,
                    timeout=PREFLIGHT_LOAD_TIMEOUT_SECONDS,
                )
                raw_count = (
                    raw_stats.get("row_count")
                    if isinstance(raw_stats, Mapping)
                    else None
                )
                row_count = _row_count(raw_count)
                if row_count > 0:
                    _require_collection_loadable(client, collection_name)
            stats[collection_name] = {"存在": exists, "数据量": row_count}
    except EvaluationPreflightError:
        raise
    except Exception:
        raise EvaluationPreflightError(
            "Docker Milvus 连接或读取统计失败"
        ) from None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    if not any(int(row["数据量"]) > 0 for row in stats.values()):
        raise EvaluationPreflightError("Docker Milvus 目标 collection 均为空")
    primary_stats = stats.get(config.milvus_collection)
    if primary_stats is None or int(primary_stats["数据量"]) <= 0:
        raise EvaluationPreflightError(
            f"Docker Milvus 统一 collection 为空：{config.milvus_collection}"
        )
    return stats


def _require_collection_loadable(client: Any, collection_name: str) -> None:
    try:
        client.load_collection(
            collection_name=collection_name,
            timeout=PREFLIGHT_LOAD_TIMEOUT_SECONDS,
        )
        raw_state = client.get_load_state(
            collection_name=collection_name,
            timeout=PREFLIGHT_LOAD_TIMEOUT_SECONDS,
        )
    except Exception:
        raise EvaluationPreflightError(
            f"Docker Milvus collection 无法加载：{collection_name}"
        ) from None

    state = raw_state.get("state") if isinstance(raw_state, Mapping) else None
    if state != LoadState.Loaded:
        raise EvaluationPreflightError(
            f"Docker Milvus collection 无法加载：{collection_name}"
        )


def _row_count(value: object) -> int:
    if isinstance(value, bool):
        raise EvaluationPreflightError("Docker Milvus collection 数据量无效")
    if isinstance(value, int):
        count = value
    elif isinstance(value, str) and value.strip().isdigit():
        count = int(value.strip())
    else:
        raise EvaluationPreflightError("Docker Milvus collection 数据量无效")
    if count < 0:
        raise EvaluationPreflightError("Docker Milvus collection 数据量无效")
    return count


def run_one_item(
    item: EvaluationItem,
    *,
    judge: _Judge,
    answer_fn: AnswerFunction = answer_question,
    top_n: int = 20,
    top_k: int = 5,
    collections: Sequence[str] | None = None,
    project_root: Path | None = None,
    chunk_catalog: set[str] | None = None,
    max_attempts: int = 3,
    sleep_fn: SleepFunction = time.sleep,
) -> EvaluationResult:
    _require_positive_integer(max_attempts, "max_attempts")
    outcome = _answer_stage(
        item,
        answer_fn=answer_fn,
        top_n=top_n,
        top_k=top_k,
        collections=collections,
        project_root=project_root,
        chunk_catalog=chunk_catalog or set(),
        max_attempts=max_attempts,
        sleep_fn=sleep_fn,
    )
    if outcome.terminal_result is not None:
        return outcome.terminal_result
    return _judge_stage(
        outcome,
        judge=judge,
        max_attempts=max_attempts,
        sleep_fn=sleep_fn,
    )


def run_items(
    items: Sequence[EvaluationItem],
    *,
    judge: _Judge,
    run_dir: Path,
    fingerprint: str,
    resume: bool = False,
    answer_fn: AnswerFunction = answer_question,
    top_n: int = 20,
    top_k: int = 5,
    collections: Sequence[str] | None = None,
    project_root: Path | None = None,
    chunk_catalog: set[str] | None = None,
    concurrency: int = 2,
    judge_concurrency: int = 2,
    max_attempts: int = 3,
    sleep_fn: SleepFunction = time.sleep,
    config_payload: Mapping[str, object] | None = None,
) -> list[EvaluationResult]:
    _require_positive_integer(concurrency, "concurrency")
    _require_positive_integer(judge_concurrency, "judge_concurrency")
    _require_positive_integer(max_attempts, "max_attempts")

    current_items = list(items)
    item_ids = [item.item_id for item in current_items]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("评测项ID重复")
    expected_item_ids = set(item_ids)
    results_path = run_dir / "results.jsonl"
    metadata_path = run_dir / "run.json"

    if resume:
        validate_resume(run_dir, expected_fingerprint=fingerprint)
        prior_results = load_checkpoint_results(
            results_path,
            expected_item_ids=expected_item_ids,
        )
    else:
        if results_path.exists() or metadata_path.exists():
            raise ValueError("运行目录已有评测结果，不能静默覆盖")
        prior_results = {}
        write_run_metadata(
            run_dir,
            fingerprint=fingerprint,
            config_payload=config_payload,
        )

    results_by_id: dict[str, EvaluationResult] = {}
    answer_items: list[EvaluationItem] = []
    judge_outcomes: list[_AnswerStageOutcome] = []
    for item in current_items:
        prior = prior_results.get(item.item_id)
        if prior is None:
            answer_items.append(item)
        elif prior.status in {"已完成", "问答失败"}:
            results_by_id[item.item_id] = prior
        elif prior.status == "评测失败":
            if prior.deterministic_scores is None:
                raise ValueError("检查点评测失败结果缺少确定性指标")
            judge_outcomes.append(
                _AnswerStageOutcome(
                    item=item,
                    answer_response=prior.answer_response,
                    duration_seconds=prior.duration_seconds,
                    deterministic_scores=prior.deterministic_scores,
                )
            )

    writer = _CheckpointWriter(results_path)
    with writer:
        if answer_items:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [
                    executor.submit(
                        _answer_stage,
                        item,
                        answer_fn=answer_fn,
                        top_n=top_n,
                        top_k=top_k,
                        collections=collections,
                        project_root=project_root,
                        chunk_catalog=chunk_catalog or set(),
                        max_attempts=max_attempts,
                        sleep_fn=sleep_fn,
                    )
                    for item in answer_items
                ]
                for future in as_completed(futures):
                    outcome = future.result()
                    if outcome.terminal_result is not None:
                        terminal = outcome.terminal_result
                        results_by_id[terminal.item_id] = terminal
                        writer.append(terminal)
                    else:
                        judge_outcomes.append(outcome)

        if judge_outcomes:
            with ThreadPoolExecutor(max_workers=judge_concurrency) as executor:
                futures = [
                    executor.submit(
                        _judge_stage,
                        outcome,
                        judge=judge,
                        max_attempts=max_attempts,
                        sleep_fn=sleep_fn,
                    )
                    for outcome in judge_outcomes
                ]
                for future in as_completed(futures):
                    terminal = future.result()
                    results_by_id[terminal.item_id] = terminal
                    writer.append(terminal)

    return sorted(results_by_id.values(), key=lambda result: result.excel_row)


def _answer_stage(
    item: EvaluationItem,
    *,
    answer_fn: AnswerFunction,
    top_n: int,
    top_k: int,
    collections: Sequence[str] | None,
    project_root: Path | None,
    chunk_catalog: set[str],
    max_attempts: int,
    sleep_fn: SleepFunction,
) -> _AnswerStageOutcome:
    started_at = time.perf_counter()
    try:
        answer_response = _with_retries(
            lambda: _call_answer(
                item,
                answer_fn=answer_fn,
                top_n=top_n,
                top_k=top_k,
                collections=collections,
                project_root=project_root,
            ),
            max_attempts=max_attempts,
            sleep_fn=sleep_fn,
        )
        deterministic_scores = score_deterministic(
            item,
            answer_response,
            chunk_catalog,
        )
    except Exception:
        duration_seconds = time.perf_counter() - started_at
        terminal = aggregate_result(
            item=item,
            answer_response={},
            duration_seconds=duration_seconds,
            answer_error=ANSWER_FAILURE_SUMMARY,
        )
        return _AnswerStageOutcome(
            item=item,
            answer_response={},
            duration_seconds=duration_seconds,
            deterministic_scores=None,
            terminal_result=terminal,
        )
    return _AnswerStageOutcome(
        item=item,
        answer_response=answer_response,
        duration_seconds=time.perf_counter() - started_at,
        deterministic_scores=deterministic_scores,
    )


def _judge_stage(
    outcome: _AnswerStageOutcome,
    *,
    judge: _Judge,
    max_attempts: int,
    sleep_fn: SleepFunction,
) -> EvaluationResult:
    started_at = time.perf_counter()
    try:
        judge_result = _with_retries(
            lambda: judge.evaluate(outcome.item, outcome.answer_response),
            max_attempts=max_attempts,
            sleep_fn=sleep_fn,
        )
    except Exception:
        return aggregate_result(
            item=outcome.item,
            answer_response=outcome.answer_response,
            duration_seconds=(
                outcome.duration_seconds + time.perf_counter() - started_at
            ),
            deterministic_scores=outcome.deterministic_scores,
            judge_error=JUDGE_FAILURE_SUMMARY,
        )
    return aggregate_result(
        item=outcome.item,
        answer_response=outcome.answer_response,
        duration_seconds=(
            outcome.duration_seconds + time.perf_counter() - started_at
        ),
        deterministic_scores=outcome.deterministic_scores,
        judge_result=judge_result,
    )


class _CheckpointWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._stream: Any | None = None

    def __enter__(self) -> _CheckpointWriter:
        _ensure_directory(self.path.parent)
        existed = self.path.exists()
        stream = self.path.open("a", encoding="utf-8")
        try:
            if not existed:
                _fsync_directory(self.path.parent)
        except Exception:
            stream.close()
            raise
        self._stream = stream
        return self

    def __exit__(self, *args: object) -> None:
        del args
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def append(self, result: EvaluationResult) -> None:
        line = json.dumps(
            dump_chinese(result),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._lock:
            if self._stream is None:
                raise RuntimeError("检查点写入器尚未打开")
            self._stream.write(line + "\n")
            self._stream.flush()
            os.fsync(self._stream.fileno())


def _call_answer(
    item: EvaluationItem,
    *,
    answer_fn: AnswerFunction,
    top_n: int,
    top_k: int,
    collections: Sequence[str] | None,
    project_root: Path | None,
) -> dict[str, Any]:
    response = answer_fn(
        query=item.question,
        top_n=top_n,
        top_k=top_k,
        collections=collections,
        project_root=project_root,
    )
    if not isinstance(response, dict):
        raise ValueError("问答结果必须是对象")
    return response


def _with_retries(
    operation: Callable[[], Any],
    *,
    max_attempts: int,
    sleep_fn: SleepFunction,
) -> Any:
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt >= max_attempts or not _is_retryable(exc):
                raise
            sleep_fn(min(0.25 * (2 ** (attempt - 1)), 2.0))
    raise AssertionError("重试循环不应到达此处")


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (ConfigError, ValueError, JudgeEvaluationError)):
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or 500 <= status_code <= 599
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(
        exc,
        (ConnectError, ConnectionNotExistException, MilvusUnavailableException),
    ):
        return True
    return (
        isinstance(exc, MilvusException)
        and getattr(exc, "code", None) == Status.CONNECT_FAILED
    )


def _require_positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return value


def compute_run_fingerprint(
    *,
    input_sha256: str,
    scoring_version: str,
    top_n: int,
    top_k: int,
    answer_model_name: str,
    judge_model_name: str,
    same_model_judge: bool,
    milvus_uri: str,
    collection_stats: Mapping[str, Mapping[str, object]],
) -> str:
    normalized_input_sha256 = _fingerprint_sha256(input_sha256)
    normalized_scoring_version = _fingerprint_text(scoring_version)
    normalized_answer_model_name = _fingerprint_text(answer_model_name)
    normalized_judge_model_name = _fingerprint_text(judge_model_name)
    normalized_top_n = _fingerprint_limit(top_n, maximum=100)
    normalized_top_k = _fingerprint_limit(top_k, maximum=20)
    if normalized_top_k > normalized_top_n:
        raise ValueError("运行指纹配置无效：top_k 不能大于 top_n")
    if not isinstance(same_model_judge, bool):
        raise ValueError("运行指纹配置无效：同模型裁判必须是布尔值")
    normalized_milvus_uri = _fingerprint_milvus_uri(milvus_uri)
    normalized_collection_stats = _fingerprint_collection_stats(
        collection_stats
    )
    payload = {
        "输入SHA256": normalized_input_sha256,
        "评分版本": normalized_scoring_version,
        "初检候选数": normalized_top_n,
        "最终证据数": normalized_top_k,
        "问答模型名": normalized_answer_model_name,
        "裁判模型名": normalized_judge_model_name,
        "同模型裁判": same_model_judge,
        "Milvus地址": normalized_milvus_uri,
        "知识集合统计": normalized_collection_stats,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fingerprint_sha256(value: object) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None
    ):
        raise ValueError("运行指纹配置无效：输入SHA256必须是64位十六进制")
    return value.lower()


def _fingerprint_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("运行指纹配置无效：文本字段不能为空")
    return value.strip()


def _fingerprint_limit(value: object, *, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise ValueError(
            "运行指纹配置无效："
            f"检索数量必须是1到{maximum}之间的整数"
        )
    return value


def _fingerprint_milvus_uri(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("运行指纹配置无效：Milvus地址必须是字符串")
    normalized = value.strip().rstrip("/")
    if normalized != LOCAL_DOCKER_MILVUS_URI:
        raise ValueError(
            "运行指纹配置无效：Milvus地址必须是本机Docker地址"
        )
    return normalized


def _fingerprint_collection_stats(
    value: object,
) -> dict[str, dict[str, bool | int]]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("运行指纹配置无效：collection统计不能为空")
    normalized: dict[str, dict[str, bool | int]] = {}
    for raw_name, raw_stats in value.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError("运行指纹配置无效：collection名称不能为空")
        collection_name = raw_name.strip()
        if collection_name in normalized:
            raise ValueError("运行指纹配置无效：collection名称重复")
        if not isinstance(raw_stats, Mapping) or set(raw_stats) != {
            "存在",
            "数据量",
        }:
            raise ValueError("运行指纹配置无效：collection统计结构非法")
        exists = raw_stats["存在"]
        row_count = raw_stats["数据量"]
        if not isinstance(exists, bool):
            raise ValueError(
                "运行指纹配置无效：collection存在必须是布尔值"
            )
        if (
            isinstance(row_count, bool)
            or not isinstance(row_count, int)
            or row_count < 0
        ):
            raise ValueError(
                "运行指纹配置无效：collection数据量必须是非负整数"
            )
        normalized[collection_name] = {"存在": exists, "数据量": row_count}
    return normalized


def write_run_metadata(
    run_dir: Path,
    fingerprint: str,
    config_payload: Mapping[str, object] | None = None,
) -> Path:
    payload = {
        "运行配置指纹": fingerprint,
        "运行配置": dict(config_payload or {}),
    }
    if _contains_secret_field(payload):
        raise ValueError("运行元数据不得包含密钥或令牌")
    if _contains_english_metadata_key(payload):
        raise ValueError("运行元数据包含英文业务字段")

    _ensure_directory(run_dir)
    target = run_dir / "run.json"
    temporary = run_dir / "run.json.tmp"
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _ensure_directory(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    path.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
        _fsync_directory(created.parent)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(os.fspath(path), flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def validate_resume(run_dir: Path, expected_fingerprint: str) -> dict[str, Any]:
    path = run_dir / "run.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("断点续跑缺少合法的运行元数据") from None
    if not isinstance(payload, dict):
        raise ValueError("断点续跑缺少合法的运行元数据")
    if payload.get("运行配置指纹") != expected_fingerprint:
        raise ValueError("运行配置指纹不一致，不能断点续跑")
    return payload


def load_checkpoint_results(
    path: Path,
    *,
    expected_item_ids: set[str] | None = None,
) -> dict[str, EvaluationResult]:
    if not path.exists():
        return {}

    latest: dict[str, EvaluationResult] = {}
    try:
        stream = path.open("r", encoding="utf-8")
    except OSError:
        raise ValueError("无法读取评测检查点") from None
    with stream:
        for line_number, raw_line in enumerate(stream, start=1):
            try:
                if not raw_line.strip():
                    raise ValueError("空行")
                payload = json.loads(raw_line)
                result = load_chinese_result(payload)
                if not _is_valid_terminal_result(result):
                    raise ValueError("终态字段不一致")
            except Exception:
                raise ValueError(
                    f"检查点第 {line_number} 行不是合法的中文评测结果"
                ) from None

            if (
                expected_item_ids is not None
                and result.item_id not in expected_item_ids
            ):
                raise ValueError(
                    "检查点包含不属于当前评测集的评测项ID"
                )
            latest[result.item_id] = result
    return latest


def _is_valid_terminal_result(result: EvaluationResult) -> bool:
    if result.status == "已完成":
        return (
            result.grade in {"优秀", "合格", "不合格"}
            and result.total_score is not None
            and result.deterministic_scores is not None
            and result.judge_result is not None
        )
    if result.status == "问答失败":
        return (
            result.grade == "问答失败"
            and result.total_score == 0
            and result.deterministic_scores is None
            and result.judge_result is None
            and result.error_tags == ["问答执行失败"]
        )
    if result.status == "评测失败":
        return (
            result.grade == "评测失败"
            and result.total_score is None
            and result.deterministic_scores is not None
            and result.judge_result is None
            and result.error_tags == ["裁判执行失败"]
        )
    return False


def _contains_secret_field(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _is_secret_key(key):
                return True
            if _contains_secret_field(item):
                return True
    elif isinstance(value, str):
        try:
            parsed = urlsplit(value)
        except ValueError:
            return False
        return parsed.username is not None or parsed.password is not None
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_secret_field(item) for item in value)
    return False


def _is_secret_key(value: object) -> bool:
    text = str(value).strip()
    lowered = text.lower()
    if any(marker in lowered for marker in ("密钥", "令牌", "密码")):
        return True
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", separated)
    tokens = set(re.findall(r"[a-z0-9]+", separated.lower()))
    if tokens & {"key", "token", "secret", "password"}:
        return True
    credential_key_prefixes = (
        "private",
        "api",
        "client",
        "access",
        "auth",
        "signing",
        "encryption",
        "milvus",
    )
    for token in tokens:
        if token.endswith(("token", "secret", "password")):
            return True
        if any(f"{prefix}key" in token for prefix in credential_key_prefixes):
            return True
    return False


def _contains_english_metadata_key(
    value: object,
    *,
    allow_dynamic_keys: bool = False,
) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if (
                not allow_dynamic_keys
                and isinstance(key, str)
                and key.isascii()
                and any(character.isalpha() for character in key)
            ):
                return True
            if _contains_english_metadata_key(
                item,
                allow_dynamic_keys=(str(key) == "知识集合统计"),
            ):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_english_metadata_key(item) for item in value)
    return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="问答评测运行器")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="只读检查 Docker Milvus collection",
    )
    args = parser.parse_args(argv)
    if not args.preflight:
        parser.error("必须指定 --preflight")

    try:
        config = RetrievalConfig.from_env()
        stats = preflight_docker_milvus(config)
    except (ValueError, EvaluationPreflightError) as exc:
        print(f"Docker Milvus预检失败：{exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {"Docker Milvus预检": stats},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
