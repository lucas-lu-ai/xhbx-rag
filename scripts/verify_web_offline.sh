#!/bin/sh
set -eu

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.offline.yml}"
RUNTIME_ENV_FILE="${RUNTIME_ENV_FILE:-.env}"
SMOKE_QUERY="${SMOKE_QUERY:-保单整理有什么作用？}"
SMOKE_TIMEOUT="${SMOKE_TIMEOUT:-300}"

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
    return
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
    return
  fi
  echo "未找到 Docker Compose" >&2
  return 127
}

env_value_or_default() {
  key="$1"
  default_value="$2"
  value="$(
    sed -n "s/^[[:space:]]*${key}[[:space:]]*=//p" "$RUNTIME_ENV_FILE" \
      | tail -n 1 \
      | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
  )"
  if [ -n "$value" ]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$default_value"
  fi
}

service_status() {
  service="$1"
  container_id="$(
    ENV_FILE="$RUNTIME_ENV_FILE" compose -f "$COMPOSE_FILE" ps -q "$service" 2>/dev/null \
      || true
  )"
  if [ -z "$container_id" ]; then
    echo "missing"
    return
  fi
  docker inspect \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
    "$container_id" 2>/dev/null || echo "missing"
}

check_service() {
  service="$1"
  status="$(service_status "$service")"
  case "$status" in
    healthy|running) echo "[PASS] $service: $status" ;;
    *) echo "[FAIL] $service: $status" >&2; return 1 ;;
  esac
}

if [ ! -f "$RUNTIME_ENV_FILE" ]; then
  echo "运行配置不存在: $RUNTIME_ENV_FILE" >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "未找到 curl" >&2
  exit 127
fi

for service in etcd minio standalone api web; do
  check_service "$service"
done

ENV_FILE="$RUNTIME_ENV_FILE" compose -f "$COMPOSE_FILE" \
  run --rm --no-deps -T cli python - <<'PY'
from xhbx_rag.config import RetrievalConfig
from xhbx_rag.milvus_store import create_milvus_store

config = RetrievalConfig.from_env(require_chat=False)
collections = (("统一知识库", config.milvus_collection),)
for label, collection_name in collections:
    store = create_milvus_store(config, collection_name=collection_name)
    if not store.collection_exists():
        raise SystemExit(f"[FAIL] {label} collection 不存在: {collection_name}")
    stats = store.client.get_collection_stats(collection_name=collection_name)
    row_count = int(stats.get("row_count", 0))
    if row_count <= 0:
        raise SystemExit(f"[FAIL] {label} collection 为空: {collection_name}")
    print(f"[PASS] {label}: {collection_name}, row_count={row_count}")
PY

API_PORT="$(env_value_or_default API_PORT 8000)"
WEB_PORT="$(env_value_or_default WEB_PORT 18088)"
STATUS_BODY="$(curl -fsS "http://127.0.0.1:${API_PORT}/api/status")"
if ! printf '%s' "$STATUS_BODY" \
  | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true'; then
  echo "[FAIL] API status 未返回 ok=true" >&2
  exit 1
fi
echo "[PASS] API status: http://127.0.0.1:${API_PORT}/api/status"

curl -fsS -o /dev/null "http://127.0.0.1:${WEB_PORT}/"
echo "[PASS] Web 首页: http://127.0.0.1:${WEB_PORT}/"

ENV_FILE="$RUNTIME_ENV_FILE" compose -f "$COMPOSE_FILE" \
  exec -T -e SMOKE_QUERY="$SMOKE_QUERY" -e SMOKE_TIMEOUT="$SMOKE_TIMEOUT" api \
  python - <<'PY'
import json
import os
import urllib.request

query = os.environ["SMOKE_QUERY"]
timeout = float(os.environ["SMOKE_TIMEOUT"])
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/answer",
    data=json.dumps({"query": query, "top_n": 20, "top_k": 5}).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=timeout) as response:
    payload = json.load(response)
answer = str(payload.get("answer", "")).strip()
if not answer:
    raise SystemExit("[FAIL] /api/answer 未返回非空 answer")
print(f"[PASS] 真实问答冒烟验证，answer_chars={len(answer)}")
PY

echo "全部验证通过"
echo "用户访问地址: http://<服务器IP>:${WEB_PORT}/"
echo "日志命令: docker compose -f $COMPOSE_FILE logs -f --tail=200 api web"
