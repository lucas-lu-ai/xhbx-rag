from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from xhbx_rag.config import ConfigError, load_env_values


@dataclass(frozen=True)
class EvaluationConfig:
    judge_base_url: str
    judge_api_key: str = field(repr=False)
    judge_model_name: str
    judge_timeout: float
    judge_retry_attempts: int
    same_model_judge: bool


def _normalized_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("裁判模型 URL 必须是有效的 http 或 https 地址")
    return normalized


def _positive_timeout(value: object) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError("EVAL_TIMEOUT 必须是有限正数") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ConfigError("EVAL_TIMEOUT 必须是有限正数")
    return timeout


def _retry_attempts(value: object) -> int:
    if isinstance(value, bool):
        raise ConfigError("EVAL_RETRY_ATTEMPTS 必须是 0 到 10 之间的整数")
    try:
        attempts = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError("EVAL_RETRY_ATTEMPTS 必须是 0 到 10 之间的整数") from exc
    if (
        isinstance(value, float)
        or str(attempts) != str(value).strip()
        or not 0 <= attempts <= 10
    ):
        raise ConfigError("EVAL_RETRY_ATTEMPTS 必须是 0 到 10 之间的整数")
    return attempts


def load_evaluation_config(
    env: Mapping[str, str] | None = None,
    env_file: Path | None = Path(".env"),
) -> EvaluationConfig:
    values = load_env_values(env=env, env_file=env_file)
    eval_keys = ("EVAL_BASE_URL", "EVAL_API_KEY", "EVAL_MODEL_NAME")
    eval_values = [str(values.get(key, "")).strip() for key in eval_keys]
    configured = [bool(value) for value in eval_values]

    if any(configured) and not all(configured):
        raise ConfigError("EVAL_BASE_URL、EVAL_API_KEY、EVAL_MODEL_NAME 必须同时配置")

    if all(configured):
        judge_base_url, judge_api_key, judge_model_name = eval_values
    else:
        fallback_keys = ("BASE_URL", "API_KEY", "MODEL_NAME")
        fallback_values = {
            key: str(values.get(key, "")).strip() for key in fallback_keys
        }
        missing = [key for key in fallback_keys if not fallback_values[key]]
        if missing:
            raise ConfigError(f"缺少裁判模型配置: {', '.join(missing)}")
        judge_base_url = fallback_values["BASE_URL"]
        judge_api_key = fallback_values["API_KEY"]
        judge_model_name = fallback_values["MODEL_NAME"]

    judge_base_url = _normalized_url(judge_base_url)
    answer_base_url = str(values.get("BASE_URL", "")).strip().rstrip("/")
    answer_model_name = str(values.get("MODEL_NAME", "")).strip()

    return EvaluationConfig(
        judge_base_url=judge_base_url,
        judge_api_key=judge_api_key,
        judge_model_name=judge_model_name,
        judge_timeout=_positive_timeout(values.get("EVAL_TIMEOUT", "180")),
        judge_retry_attempts=_retry_attempts(
            values.get("EVAL_RETRY_ATTEMPTS", "2")
        ),
        same_model_judge=(
            judge_base_url == answer_base_url
            and judge_model_name == answer_model_name
        ),
    )
