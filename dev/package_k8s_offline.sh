#!/bin/sh
set -eu

TARGET_PLATFORM="${1:-}"
case "$TARGET_PLATFORM" in
  amd) PLATFORM_SUFFIX="amd64"; DOCKER_PLATFORM="linux/amd64" ;;
  arm) PLATFORM_SUFFIX="arm64"; DOCKER_PLATFORM="linux/arm64" ;;
  *)
    echo "用法: $0 amd|arm" >&2
    exit 2
    ;;
esac

OUTPUT_DIR="${OUTPUT_DIR:-dist}"
PACKAGE_NAME="xhbx-rag-k8s-offline-${PLATFORM_SUFFIX}"
PACKAGE_DIR="$OUTPUT_DIR/$PACKAGE_NAME"
FINAL_ARCHIVE="$OUTPUT_DIR/$PACKAGE_NAME.tar.gz"
TEMP_ARCHIVE="$OUTPUT_DIR/.$PACKAGE_NAME.tar.gz.tmp"
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/xhbx-rag-k8s.XXXXXX")"
DATA_CONTEXT="$TEMP_ROOT/data-image"

API_IMAGE="localhost/xhbx-rag-api:offline"
WEB_IMAGE="localhost/xhbx-rag-web:offline"
DATA_IMAGE="localhost/xhbx-rag-data:offline"
ETCD_IMAGE="localhost/xhbx-rag-etcd:v3.5.25"
MINIO_IMAGE="localhost/xhbx-rag-minio:RELEASE.2024-12-18T13-15-44Z"
MILVUS_IMAGE="localhost/xhbx-rag-milvus:v2.6.19"

SOURCE_ETCD_IMAGE="quay.io/coreos/etcd:v3.5.25"
SOURCE_MINIO_IMAGE="minio/minio:RELEASE.2024-12-18T13-15-44Z"
SOURCE_MILVUS_IMAGE="milvusdb/milvus:v2.6.19"

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

validate_image_platform() {
  image="$1"
  actual="$(
    docker image inspect --platform "$DOCKER_PLATFORM" "$image" \
      --format '{{.Os}}/{{.Architecture}}'
  )"
  if [ "$actual" != "$DOCKER_PLATFORM" ]; then
    echo "镜像平台不匹配: image=$image expected=$DOCKER_PLATFORM actual=$actual" >&2
    return 1
  fi
}

cleanup() {
  rm -rf "$TEMP_ROOT"
  rm -f "$TEMP_ARCHIVE"
}
trap cleanup EXIT HUP INT TERM

if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 Docker" >&2
  exit 127
fi
docker buildx version >/dev/null

for required_path in \
  Dockerfile.api \
  web/Dockerfile \
  parsed \
  dev/Dockerfile.data \
  dev/import_k8s_images.sh \
  dev/Kubernetes离线部署文档.md \
  dev/k8s
do
  if [ ! -e "$required_path" ]; then
    echo "打包所需路径不存在: $required_path" >&2
    exit 1
  fi
done

if ! find parsed -type f \( -name 'chunks.jsonl' -o -name '*.chunks.jsonl' \) \
  -print -quit | grep -q .; then
  echo "parsed 中未找到可入库的 chunk JSONL" >&2
  exit 1
fi

docker buildx build --platform "$DOCKER_PLATFORM" --load \
  -t "$API_IMAGE" -f Dockerfile.api .
docker buildx build --platform "$DOCKER_PLATFORM" --load \
  -t "$WEB_IMAGE" -f web/Dockerfile .

mkdir -p "$DATA_CONTEXT/parsed"
cp -R parsed/. "$DATA_CONTEXT/parsed/"
cp dev/Dockerfile.data "$DATA_CONTEXT/Dockerfile"
docker buildx build --platform "$DOCKER_PLATFORM" --load \
  -t "$DATA_IMAGE" "$DATA_CONTEXT"

docker pull --platform "$DOCKER_PLATFORM" "$SOURCE_ETCD_IMAGE"
docker pull --platform "$DOCKER_PLATFORM" "$SOURCE_MINIO_IMAGE"
docker pull --platform "$DOCKER_PLATFORM" "$SOURCE_MILVUS_IMAGE"
docker tag "$SOURCE_ETCD_IMAGE" "$ETCD_IMAGE"
docker tag "$SOURCE_MINIO_IMAGE" "$MINIO_IMAGE"
docker tag "$SOURCE_MILVUS_IMAGE" "$MILVUS_IMAGE"

for image in \
  "$API_IMAGE" \
  "$WEB_IMAGE" \
  "$DATA_IMAGE" \
  "$ETCD_IMAGE" \
  "$MINIO_IMAGE" \
  "$MILVUS_IMAGE"
do
  validate_image_platform "$image"
done

mkdir -p "$OUTPUT_DIR"
rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR"
cp dev/import_k8s_images.sh "$PACKAGE_DIR/import_k8s_images.sh"
cp dev/Kubernetes离线部署文档.md "$PACKAGE_DIR/Kubernetes离线部署文档.md"
cp -R dev/k8s "$PACKAGE_DIR/k8s"
chmod +x "$PACKAGE_DIR/import_k8s_images.sh"

docker save -o "$PACKAGE_DIR/images.tar" \
  "$API_IMAGE" \
  "$WEB_IMAGE" \
  "$DATA_IMAGE" \
  "$ETCD_IMAGE" \
  "$MINIO_IMAGE" \
  "$MILVUS_IMAGE"

IMAGE_CHECKSUM="$(sha256_file "$PACKAGE_DIR/images.tar")"
printf '%s  images.tar\n' "$IMAGE_CHECKSUM" > "$PACKAGE_DIR/images.sha256"

{
  printf 'package=%s\n' "$PACKAGE_NAME"
  printf 'platform=%s\n' "$DOCKER_PLATFORM"
  printf 'image=%s\n' "$API_IMAGE"
  printf 'image=%s\n' "$WEB_IMAGE"
  printf 'image=%s\n' "$DATA_IMAGE"
  printf 'image=%s\n' "$ETCD_IMAGE"
  printf 'image=%s\n' "$MINIO_IMAGE"
  printf 'image=%s\n' "$MILVUS_IMAGE"
} > "$PACKAGE_DIR/package-manifest.txt"

tar -C "$OUTPUT_DIR" -czf "$TEMP_ARCHIVE" "$PACKAGE_NAME"
mv "$TEMP_ARCHIVE" "$FINAL_ARCHIVE"

echo "Kubernetes 离线部署包已生成: $FINAL_ARCHIVE"
echo "目标平台: $DOCKER_PLATFORM"
echo "内网节点导入后不会访问镜像仓库"
