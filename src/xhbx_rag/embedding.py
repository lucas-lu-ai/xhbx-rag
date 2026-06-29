from __future__ import annotations

from typing import Protocol

import httpx


class EmbeddingError(RuntimeError):
    """Raised when embedding API response is invalid."""


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


class EmbeddingClient:
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

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.http_client.post(
            f"{self.base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"model": self.model, "input": texts},
            timeout=self.timeout,
        )
        response.raise_for_status()  # type: ignore[attr-defined]
        data = response.json()  # type: ignore[attr-defined]
        items = data.get("data", [])
        if len(items) != len(texts):
            raise EmbeddingError(
                f"embedding 返回数量不匹配: expected={len(texts)} actual={len(items)}"
            )

        vectors: list[list[float] | None] = [None] * len(texts)
        for item in items:
            index = item.get("index")
            embedding = item.get("embedding")
            if not isinstance(index, int) or not isinstance(embedding, list):
                raise EmbeddingError("embedding 响应缺少有效 index 或 embedding")
            if index < 0 or index >= len(texts):
                raise EmbeddingError(f"embedding index 越界: {index}")
            vectors[index] = [float(value) for value in embedding]

        if any(vector is None for vector in vectors):
            raise EmbeddingError("embedding 响应缺少部分输入的向量")
        return [vector for vector in vectors if vector is not None]
