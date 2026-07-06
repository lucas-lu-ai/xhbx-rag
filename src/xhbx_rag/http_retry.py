from __future__ import annotations

import time
from typing import Protocol

import httpx


class HttpClient(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: dict,
        json: dict,
        timeout: float,
    ) -> object:
        """Post JSON to an API endpoint."""


RETRYABLE_TRANSPORT_ERRORS = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.PoolTimeout,
)

# 兼容旧私有名。
_RETRYABLE_TRANSPORT_ERRORS = RETRYABLE_TRANSPORT_ERRORS


def is_retryable_status_code(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600


def sleep_before_retry(attempt: int, base_delay: float) -> None:
    if base_delay <= 0:
        return
    time.sleep(base_delay * (2 ** (attempt - 1)))


def post_json_with_retry(
    http_client: HttpClient,
    url: str,
    *,
    headers: dict,
    json: dict,
    timeout: float,
    retry_attempts: int = 3,
    retry_base_delay: float = 0.5,
) -> object:
    attempts = max(1, retry_attempts)
    for attempt in range(1, attempts + 1):
        try:
            response = http_client.post(
                url,
                headers=headers,
                json=json,
                timeout=timeout,
            )
            response.raise_for_status()  # type: ignore[attr-defined]
            return response
        except httpx.HTTPStatusError as exc:
            if attempt == attempts or not is_retryable_status_code(
                exc.response.status_code
            ):
                raise
        except RETRYABLE_TRANSPORT_ERRORS:
            if attempt == attempts:
                raise
        sleep_before_retry(attempt, retry_base_delay)
    raise RuntimeError("unreachable retry state")
