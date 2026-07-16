#!/bin/sh
set -eu

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.offline.yml}"
IMAGE_TAR="${IMAGE_TAR:-images.tar}"
CHECKSUM_FILE="${CHECKSUM_FILE:-images.sha256}"
MANIFEST_FILE="${MANIFEST_FILE:-package-manifest.txt}"
ENV_TEMPLATE="${ENV_TEMPLATE:-.env.offline.example}"
RUNTIME_ENV_FILE="${RUNTIME_ENV_FILE:-.env}"
VERIFY_SCRIPT="${VERIFY_SCRIPT:-verify_web_offline.sh}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-300}"
DEPLOY_STARTED="false"

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
    return
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
    return
  fi
  echo "未找到 Docker Compose，请安装 docker compose 插件" >&2
  return 127
}

sha256_file() {
  file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
    return
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
    return
  fi
  echo "未找到 sha256sum 或 shasum" >&2
  return 127
}

manifest_value() {
  key="$1"
  sed -n "s/^${key}=//p" "$MANIFEST_FILE" | sed -n '1p'
}

verify_checksum() {
  if [ ! -f "$IMAGE_TAR" ]; then
    echo "镜像包不存在: $IMAGE_TAR" >&2
    return 1
  fi
  if [ ! -f "$CHECKSUM_FILE" ]; then
    echo "镜像校验文件不存在: $CHECKSUM_FILE" >&2
    return 1
  fi
  expected="$(awk 'NF {print $1; exit}' "$CHECKSUM_FILE")"
  actual="$(sha256_file "$IMAGE_TAR")"
  if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
    echo "images.tar SHA-256 校验失败" >&2
    return 1
  fi
  echo "镜像包 SHA-256 校验通过"
}

validate_platform() {
  if [ ! -f "$MANIFEST_FILE" ]; then
    echo "离线包清单不存在: $MANIFEST_FILE" >&2
    return 1
  fi

  case "$(uname -m)" in
    x86_64|amd64) actual_platform="linux/amd64" ;;
    aarch64|arm64) actual_platform="linux/arm64" ;;
    *)
      echo "不支持的服务器架构: $(uname -m)" >&2
      return 1
      ;;
  esac

  expected_platform="$(manifest_value platform)"
  if [ -z "$expected_platform" ]; then
    echo "package-manifest.txt 缺少 platform" >&2
    return 1
  fi
  if [ "$actual_platform" != "$expected_platform" ]; then
    echo "离线包平台不匹配: package=$expected_platform server=$actual_platform" >&2
    return 1
  fi
  echo "服务器架构校验通过: $actual_platform"
}

env_value() {
  key="$1"
  sed -n "s/^[[:space:]]*${key}[[:space:]]*=//p" "$RUNTIME_ENV_FILE" \
    | tail -n 1 \
    | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

validate_env() {
  required_keys="
API_KEY
BASE_URL
MODEL_NAME
EMBEDDING_BASE_URL
EMBEDDING_MODEL_NAME
EMBEDDING_API_KEY
RERANK_BASE_URL
RERANK_MODEL_NAME
RERANK_API_KEY
"
  missing=""
  for key in $required_keys; do
    value="$(env_value "$key")"
    case "$value" in
      ""|'""'|"''") missing="$missing $key" ;;
    esac
  done
  if [ -n "$missing" ]; then
    echo "缺少必要环境变量:$missing" >&2
    return 1
  fi
  echo "模型与检索配置字段校验通过"
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

wait_for_service() {
  service="$1"
  elapsed=0
  while [ "$elapsed" -lt "$WAIT_TIMEOUT" ]; do
    status="$(service_status "$service")"
    case "$status" in
      healthy|running)
        echo "服务已就绪: $service ($status)"
        return 0
        ;;
      unhealthy|exited|dead)
        echo "服务启动失败: $service ($status)" >&2
        ENV_FILE="$RUNTIME_ENV_FILE" compose -f "$COMPOSE_FILE" logs --tail=100 "$service" >&2 || true
        return 1
        ;;
    esac
    sleep 2
    elapsed=$((elapsed + 2))
  done
  echo "等待服务超时: $service (${WAIT_TIMEOUT}s)" >&2
  ENV_FILE="$RUNTIME_ENV_FILE" compose -f "$COMPOSE_FILE" logs --tail=100 "$service" >&2 || true
  return 1
}

on_exit() {
  status=$?
  if [ "$status" -ne 0 ] && [ "$DEPLOY_STARTED" = "true" ]; then
    echo "离线部署失败。当前服务状态：" >&2
    ENV_FILE="$RUNTIME_ENV_FILE" compose -f "$COMPOSE_FILE" ps >&2 || true
    echo "查看日志：docker compose -f $COMPOSE_FILE logs --tail=200 <service>" >&2
  fi
  exit "$status"
}
trap on_exit EXIT

# 主部署流程
if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 Docker" >&2
  exit 127
fi
compose version >/dev/null
if ! command -v curl >/dev/null 2>&1; then
  echo "未找到 curl" >&2
  exit 127
fi
for required_file in "$COMPOSE_FILE" "$ENV_TEMPLATE"; do
  if [ ! -f "$required_file" ]; then
    echo "部署文件不存在: $required_file" >&2
    exit 1
  fi
done
if [ ! -f "$RUNTIME_ENV_FILE" ]; then
  cp "$ENV_TEMPLATE" "$RUNTIME_ENV_FILE"
  echo "已生成 $RUNTIME_ENV_FILE，请填写三个内网模型服务配置后重新执行本脚本"
  exit 2
fi

verify_checksum
validate_platform
validate_env
ENV_FILE="$RUNTIME_ENV_FILE" compose -f "$COMPOSE_FILE" config --quiet

DEPLOY_STARTED="true"
docker load -i "$IMAGE_TAR"
mkdir -p data .local

ENV_FILE="$RUNTIME_ENV_FILE" compose -f "$COMPOSE_FILE" \
  up -d --no-build etcd minio standalone
wait_for_service etcd
wait_for_service minio
wait_for_service standalone

# 重建 collection 前停止可能存在的旧 API/Web，避免更新期间读取半成品索引。
ENV_FILE="$RUNTIME_ENV_FILE" compose -f "$COMPOSE_FILE" stop api web

ENV_FILE="$RUNTIME_ENV_FILE" compose -f "$COMPOSE_FILE" \
  run --rm --no-deps cli sh /app/scripts/index_parsed_offline.sh all

ENV_FILE="$RUNTIME_ENV_FILE" compose -f "$COMPOSE_FILE" \
  up -d --no-build api web
wait_for_service api
wait_for_service web

if [ ! -f "$VERIFY_SCRIPT" ] && [ -f "scripts/verify_web_offline.sh" ]; then
  VERIFY_SCRIPT="scripts/verify_web_offline.sh"
fi
sh "$VERIFY_SCRIPT"

DEPLOY_STARTED="false"
echo "xhbx-rag Web 离线部署完成"
