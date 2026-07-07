#!/bin/sh
set -eu

IMAGE_TAR="${IMAGE_TAR:-images.tar}"

compose() {
  if command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
    return
  fi
  docker compose "$@"
}

if [ ! -f "$IMAGE_TAR" ]; then
  echo "镜像包不存在: $IMAGE_TAR" >&2
  exit 1
fi

docker load -i "$IMAGE_TAR"

if [ ! -f .env.mcp ]; then
  cp .env.mcp.example .env.mcp
  echo "已生成 .env.mcp，请填入真实配置后重新执行: sh load_mcp_offline.sh"
  exit 0
fi

compose -f docker-compose.mcp.yml up -d --no-build

echo "MCP 服务已启动。状态检查: docker-compose -f docker-compose.mcp.yml ps"
