#!/bin/sh
set -eu

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.mcp.yml}"
OUTPUT_DIR="${OUTPUT_DIR:-dist}"
PACKAGE_NAME="${PACKAGE_NAME:-xhbx-rag-mcp-offline}"
INCLUDE_PARSED="${INCLUDE_PARSED:-true}"
PACKAGE_DIR="$OUTPUT_DIR/$PACKAGE_NAME"
ENV_CREATED="false"

DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
DOCKER_DEFAULT_PLATFORM="${DOCKER_DEFAULT_PLATFORM:-$DOCKER_PLATFORM}"
DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}"
COMPOSE_DOCKER_CLI_BUILD="${COMPOSE_DOCKER_CLI_BUILD:-1}"
export DOCKER_PLATFORM DOCKER_DEFAULT_PLATFORM DOCKER_BUILDKIT COMPOSE_DOCKER_CLI_BUILD

compose() {
  if command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
    return
  fi
  docker compose "$@"
}

EXPECTED_OS="${DOCKER_PLATFORM%%/*}"
EXPECTED_ARCH="${DOCKER_PLATFORM#*/}"
EXPECTED_ARCH="${EXPECTED_ARCH%%/*}"

validate_image_platform() {
  image="$1"
  actual="$(
    docker image inspect --platform "$DOCKER_PLATFORM" "$image" \
      --format '{{.Os}}/{{.Architecture}}'
  )"
  expected="$EXPECTED_OS/$EXPECTED_ARCH"
  if [ "$actual" != "$expected" ]; then
    echo "镜像平台不匹配: $image expected=$expected actual=$actual" >&2
    exit 1
  fi
}

cleanup() {
  if [ "$ENV_CREATED" = "true" ]; then
    rm -f .env.mcp
  fi
}
trap cleanup EXIT

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "compose 文件不存在: $COMPOSE_FILE" >&2
  exit 1
fi

if [ ! -f .env.mcp ]; then
  cp .env.mcp.example .env.mcp
  ENV_CREATED="true"
fi

rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR/scripts"

docker buildx build --platform "$DOCKER_PLATFORM" --load -t xhbx-rag-mcp:latest -f Dockerfile.api .

for image in \
  quay.io/coreos/etcd:v3.5.25 \
  minio/minio:RELEASE.2024-12-18T13-15-44Z \
  milvusdb/milvus:v2.6.19
do
  docker pull --platform "$DOCKER_PLATFORM" "$image"
done

IMAGES="$(compose -f "$COMPOSE_FILE" config --images 2>/dev/null | sort -u || true)"
if [ -z "$IMAGES" ]; then
  IMAGES="xhbx-rag-mcp:latest
quay.io/coreos/etcd:v3.5.25
minio/minio:RELEASE.2024-12-18T13-15-44Z
milvusdb/milvus:v2.6.19"
fi
if [ -z "$IMAGES" ]; then
  echo "未从 compose 配置中解析到镜像" >&2
  exit 1
fi

for image in $IMAGES; do
  validate_image_platform "$image"
done

docker save --platform "$DOCKER_PLATFORM" -o "$PACKAGE_DIR/images.tar" $IMAGES

cp "$COMPOSE_FILE" "$PACKAGE_DIR/docker-compose.mcp.yml"
cp .env.mcp.example "$PACKAGE_DIR/.env.mcp.example"
cp scripts/index_parsed.sh "$PACKAGE_DIR/scripts/index_parsed.sh"
cp scripts/test_mcp.sh "$PACKAGE_DIR/scripts/test_mcp.sh"
cp scripts/debug_mcp_search.sh "$PACKAGE_DIR/scripts/debug_mcp_search.sh"
cp scripts/load_mcp_offline.sh "$PACKAGE_DIR/load_mcp_offline.sh"
chmod +x "$PACKAGE_DIR/load_mcp_offline.sh" "$PACKAGE_DIR/scripts/index_parsed.sh" "$PACKAGE_DIR/scripts/test_mcp.sh" "$PACKAGE_DIR/scripts/debug_mcp_search.sh"

if [ "$INCLUDE_PARSED" = "true" ] && [ -d parsed ]; then
  cp -R parsed "$PACKAGE_DIR/parsed"
fi

cat > "$PACKAGE_DIR/README.offline.md" <<'EOF'
# xhbx-rag MCP 离线部署包

## 1. 导入镜像并准备环境变量

```bash
sh load_mcp_offline.sh
```

如果首次执行时生成了 `.env.mcp`，请填入真实模型、embedding、rerank 配置后再次执行：

```bash
sh load_mcp_offline.sh
```

离线导入镜像只解决 Docker 拉取/构建问题。`scripts/index_parsed.sh` 入库需要访问
`EMBEDDING_BASE_URL`，MCP 检索需要访问 `BASE_URL`、`EMBEDDING_BASE_URL`
和 `RERANK_BASE_URL`。如果服务器完全不能访问这些接口，请改用内网模型服务地址，
或先在有网环境完成入库后再迁移 Milvus 数据卷。

这个包默认按 `linux/amd64` 打包，适合普通 x86_64 Linux 服务器。如果目标服务器
是 ARM64，需要在打包前设置 `DOCKER_PLATFORM=linux/arm64` 并重新生成离线包。

## 2. 批量入库 parsed

增量入库：

```bash
docker-compose -f docker-compose.mcp.yml exec mcp scripts/index_parsed.sh
```

首次全量重建：

```bash
docker-compose -f docker-compose.mcp.yml exec -e RESET_COLLECTION=true mcp scripts/index_parsed.sh
```

## 3. 测试 MCP 服务

只测服务和工具列表：

```bash
scripts/test_mcp.sh
```

带检索问题测试：

```bash
scripts/test_mcp.sh "客户说预算不够怎么办？"
```

如果 `kb_search_knowledge` 只返回安全兜底错误，用下面命令查看容器内真实异常：

```bash
scripts/debug_mcp_search.sh "客户说预算不够怎么办？"
```

## 4. MCP 客户端地址

默认监听宿主机 `127.0.0.1:9331`。如需可信内网直连，在 `.env.mcp` 中设置：

```env
MCP_BIND=0.0.0.0
```

Tool 暴露模式也在 `.env.mcp` 中设置：

```env
MCP_TOOL_PROFILE=kb      # 默认，只暴露 kb_* 工具
MCP_TOOL_PROFILE=legacy  # 只暴露旧 search_knowledge / retrieval_status / list_filter_options
MCP_TOOL_PROFILE=both    # 新旧工具都暴露
```

客户端 URL：

```text
http://服务器IP:9331/mcp
```
EOF

mkdir -p "$OUTPUT_DIR"
tar -C "$OUTPUT_DIR" -czf "$OUTPUT_DIR/$PACKAGE_NAME.tar.gz" "$PACKAGE_NAME"

echo "离线部署包已生成: $OUTPUT_DIR/$PACKAGE_NAME.tar.gz"
echo "传到服务器后执行: tar -xzf $PACKAGE_NAME.tar.gz && cd $PACKAGE_NAME && sh load_mcp_offline.sh"
