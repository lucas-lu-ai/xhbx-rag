import httpx

from xhbx_rag.embedding import EmbeddingClient
from xhbx_rag.rerank import RerankClient


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict, timeout: float) -> _FakeResponse:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        return _FakeResponse(self.payload)


class _FlakyHttpClient:
    def __init__(self, failures: list[Exception], payload: dict) -> None:
        self.failures = failures
        self.payload = payload
        self.calls: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict, timeout: float) -> _FakeResponse:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        if self.failures:
            raise self.failures.pop(0)
        return _FakeResponse(self.payload)


def test_embedding_client_posts_siliconflow_shape_and_restores_order() -> None:
    http = _FakeHttpClient(
        {
            "data": [
                {"embedding": [0.2, 0.3], "index": 1},
                {"embedding": [0.1, 0.4], "index": 0},
            ]
        }
    )
    client = EmbeddingClient(
        base_url="https://api.siliconflow.com/v1",
        api_key="secret",
        model="Qwen/Qwen3-Embedding-8B",
        http_client=http,
    )

    vectors = client.embed_documents(["文本1", "文本2"])

    assert vectors == [[0.1, 0.4], [0.2, 0.3]]
    call = http.calls[0]
    assert call["url"] == "https://api.siliconflow.com/v1/embeddings"
    assert call["headers"]["Authorization"] == "Bearer secret"
    assert call["json"] == {
        "model": "Qwen/Qwen3-Embedding-8B",
        "input": ["文本1", "文本2"],
    }


def test_embedding_client_accepts_full_endpoint_url() -> None:
    http = _FakeHttpClient({"data": [{"embedding": [0.1, 0.4], "index": 0}]})
    client = EmbeddingClient(
        base_url="https://api.siliconflow.com/v1/embeddings",
        api_key="secret",
        model="Qwen/Qwen3-Embedding-8B",
        http_client=http,
    )

    client.embed_query("文本1")

    assert http.calls[0]["url"] == "https://api.siliconflow.com/v1/embeddings"


def test_embedding_client_retries_transient_http_errors() -> None:
    http = _FlakyHttpClient(
        failures=[httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING]")],
        payload={"data": [{"embedding": [0.1, 0.4], "index": 0}]},
    )
    client = EmbeddingClient(
        base_url="https://api.siliconflow.com/v1",
        api_key="secret",
        model="Qwen/Qwen3-Embedding-8B",
        http_client=http,
        retry_base_delay=0,
    )

    vector = client.embed_query("文本1")

    assert vector == [0.1, 0.4]
    assert len(http.calls) == 2


def test_rerank_client_returns_top_k_ranked_results() -> None:
    http = _FakeHttpClient(
        {
            "results": [
                {"index": 2, "relevance_score": 0.95, "document": {"text": "fruit"}},
                {"index": 0, "relevance_score": 0.6, "document": {"text": "apple"}},
            ]
        }
    )
    client = RerankClient(
        base_url="https://api.siliconflow.com/v1/",
        api_key="secret",
        model="Qwen/Qwen3-Reranker-8B",
        http_client=http,
    )

    results = client.rerank("Apple", ["apple", "banana", "fruit"], top_k=1)

    assert len(results) == 1
    assert results[0].index == 2
    assert results[0].relevance_score == 0.95
    call = http.calls[0]
    assert call["url"] == "https://api.siliconflow.com/v1/rerank"
    assert call["json"] == {
        "model": "Qwen/Qwen3-Reranker-8B",
        "query": "Apple",
        "documents": ["apple", "banana", "fruit"],
    }


def test_rerank_client_accepts_full_endpoint_url() -> None:
    http = _FakeHttpClient(
        {
            "results": [
                {"index": 0, "relevance_score": 0.6, "document": {"text": "apple"}},
            ]
        }
    )
    client = RerankClient(
        base_url="https://api.siliconflow.com/v1/rerank",
        api_key="secret",
        model="Qwen/Qwen3-Reranker-8B",
        http_client=http,
    )

    client.rerank("Apple", ["apple"], top_k=1)

    assert http.calls[0]["url"] == "https://api.siliconflow.com/v1/rerank"


def test_rerank_client_retries_transient_http_errors() -> None:
    http = _FlakyHttpClient(
        failures=[httpx.ReadTimeout("read timed out")],
        payload={
            "results": [
                {"index": 0, "relevance_score": 0.6, "document": {"text": "apple"}},
            ]
        },
    )
    client = RerankClient(
        base_url="https://api.siliconflow.com/v1",
        api_key="secret",
        model="Qwen/Qwen3-Reranker-8B",
        http_client=http,
        retry_base_delay=0,
    )

    results = client.rerank("Apple", ["apple"], top_k=1)

    assert results[0].text == "apple"
    assert len(http.calls) == 2
