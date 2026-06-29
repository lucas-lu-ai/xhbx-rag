from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx


class RerankError(RuntimeError):
    """Raised when rerank API response is invalid."""


class _HttpClient(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: dict,
        json: dict,
        timeout: float,
    ) -> object:
        """Post JSON to an API endpoint."""


@dataclass(frozen=True)
class RerankResult:
    index: int
    relevance_score: float
    text: str


class RerankClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        http_client: _HttpClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.http_client = http_client or httpx.Client()
        self.timeout = timeout

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        if not documents or top_k <= 0:
            return []
        response = self.http_client.post(
            f"{self.base_url}/rerank",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "query": query,
                "documents": documents,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()  # type: ignore[attr-defined]
        payload = response.json()  # type: ignore[attr-defined]
        results = []
        for item in payload.get("results", []):
            index = item.get("index")
            score = item.get("relevance_score")
            document = item.get("document", {})
            text = document.get("text", "") if isinstance(document, dict) else ""
            if not isinstance(index, int):
                raise RerankError("rerank 响应缺少有效 index")
            if index < 0 or index >= len(documents):
                raise RerankError(f"rerank index 越界: {index}")
            results.append(
                RerankResult(
                    index=index,
                    relevance_score=float(score),
                    text=str(text),
                )
            )
        results.sort(key=lambda item: item.relevance_score, reverse=True)
        return results[:top_k]
