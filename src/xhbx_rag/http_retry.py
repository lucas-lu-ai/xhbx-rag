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


_RETRYABLE_TRANSPORT_ERRORS = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.PoolTimeout,
)


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
            if attempt == attempts or not _is_retryable_status(exc):
                raise
        except _RETRYABLE_TRANSPORT_ERRORS:
            if attempt == attempts:
                raise
        _sleep_before_retry(attempt, retry_base_delay)
    raise RuntimeError("unreachable retry state")


def _is_retryable_status(exc: httpx.HTTPStatusError) -> bool:
    status_code = exc.response.status_code
    return status_code == 429 or 500 <= status_code < 600


def _sleep_before_retry(attempt: int, base_delay: float) -> None:
    if base_delay <= 0:
        return
    time.sleep(base_delay * (2 ** (attempt - 1)))
