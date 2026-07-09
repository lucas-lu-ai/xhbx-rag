#!/bin/sh
set -eu

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.mcp.yml}"
QUERY="${1:-${QUERY:-客户说预算不够怎么办？}}"
TOP_N="${TOP_N:-20}"
TOP_K="${TOP_K:-5}"

compose() {
  if command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
    return
  fi
  docker compose "$@"
}

compose -f "$COMPOSE_FILE" exec -T \
  -e DEBUG_QUERY="$QUERY" \
  -e DEBUG_TOP_N="$TOP_N" \
  -e DEBUG_TOP_K="$TOP_K" \
  mcp python - <<'PY'
import os
import traceback

from xhbx_rag.config import RetrievalConfig
from xhbx_rag.embedding import EmbeddingClient
from xhbx_rag.mcp_server import _direct_search_evidence
from xhbx_rag.milvus_store import create_retrieval_store
from xhbx_rag.rerank import RerankClient

query = os.environ["DEBUG_QUERY"]
top_n = int(os.environ.get("DEBUG_TOP_N", "20"))
top_k = int(os.environ.get("DEBUG_TOP_K", "5"))

try:
    config = RetrievalConfig.from_env(require_chat=False)
    print(config.safe_summary())

    embedding = EmbeddingClient(
        base_url=config.embedding_base_url,
        api_key=config.embedding_api_key,
        model=config.embedding_model_name,
    )
    store = create_retrieval_store(config)
    reranker = RerankClient(
        base_url=config.rerank_base_url,
        api_key=config.rerank_api_key,
        model=config.rerank_model_name,
    )

    result = _direct_search_evidence(
        query=query,
        embedding_client=embedding,
        store=store,
        reranker=reranker,
        top_n=top_n,
        top_k=top_k,
        filters={},
    )
    print(result)
except Exception:
    traceback.print_exc()
    raise
PY
