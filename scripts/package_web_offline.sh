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
PACKAGE_NAME="xhbx-rag-web-offline-${PLATFORM_SUFFIX}"
PACKAGE_DIR="$OUTPUT_DIR/$PACKAGE_NAME"
FINAL_ARCHIVE="$OUTPUT_DIR/$PACKAGE_NAME.tar.gz"
TEMP_ARCHIVE="$OUTPUT_DIR/.$PACKAGE_NAME.tar.gz.tmp"
LIST_DIR="${TMPDIR:-/tmp}/xhbx-rag-web-package.$$"
CASE_LIST="$LIST_DIR/case-files.txt"
COURSE_LIST="$LIST_DIR/course-files.txt"
IMAGES="xhbx-rag-api:latest
xhbx-rag-web:latest
quay.io/coreos/etcd:v3.5.25
minio/minio:RELEASE.2024-12-18T13-15-44Z
milvusdb/milvus:v2.6.19"

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
    echo "镜像平台不匹配: $image expected=$DOCKER_PLATFORM actual=$actual" >&2
    return 1
  fi
}

build_chunk_lists() {
  : > "$CASE_LIST"
  for chunks_file in parsed/*/chunks.jsonl; do
    [ -f "$chunks_file" ] || continue
    case "$chunks_file" in
      parsed/chunk/*) continue ;;
    esac
    printf '%s\n' "$chunks_file" >> "$CASE_LIST"
  done
  LC_ALL=C sort -o "$CASE_LIST" "$CASE_LIST"

  : > "$COURSE_LIST"
  for chunks_file in parsed/chunk/*.chunks.jsonl; do
    [ -f "$chunks_file" ] || continue
    printf '%s\n' "$chunks_file" >> "$COURSE_LIST"
  done
  LC_ALL=C sort -o "$COURSE_LIST" "$COURSE_LIST"

  if [ ! -s "$CASE_LIST" ]; then
    echo "未找到案例数据: parsed/*/chunks.jsonl" >&2
    return 1
  fi
  if [ ! -s "$COURSE_LIST" ]; then
    echo "未找到课程数据: parsed/chunk/*.chunks.jsonl" >&2
    return 1
  fi
}

count_lines() {
  list_file="$1"
  total=0
  while IFS= read -r source_file; do
    lines="$(wc -l < "$source_file" | tr -d '[:space:]')"
    total=$((total + lines))
  done < "$list_file"
  printf '%s\n' "$total"
}

copy_chunk_files() {
  list_file="$1"
  while IFS= read -r source_file; do
    target_file="$PACKAGE_DIR/$source_file"
    mkdir -p "$(dirname "$target_file")"
    cp "$source_file" "$target_file"
  done < "$list_file"
}

append_data_checksums() {
  list_file="$1"
  while IFS= read -r source_file; do
    checksum="$(sha256_file "$source_file")"
    printf 'file_sha256=%s  %s\n' "$checksum" "$source_file" \
      >> "$PACKAGE_DIR/package-manifest.txt"
  done < "$list_file"
}

cleanup() {
  rm -rf "$LIST_DIR"
  rm -f "$TEMP_ARCHIVE"
}
trap cleanup EXIT HUP INT TERM

if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 Docker" >&2
  exit 127
fi
docker buildx version >/dev/null
compose version >/dev/null

for required_file in \
  Dockerfile.api \
  web/Dockerfile \
  docker-compose.offline.yml \
  .env.offline.example \
  scripts/deploy_web_offline.sh \
  scripts/verify_web_offline.sh \
  scripts/index_parsed_offline.sh \
  docs/Web问答界面离线部署文档.md
do
  if [ ! -f "$required_file" ]; then
    echo "打包所需文件不存在: $required_file" >&2
    exit 1
  fi
done

mkdir -p "$LIST_DIR" "$OUTPUT_DIR"
build_chunk_lists
ENV_FILE=.env.offline.example compose -f docker-compose.offline.yml config --quiet

docker buildx build --platform "$DOCKER_PLATFORM" --load \
  -t xhbx-rag-api:latest -f Dockerfile.api .
docker buildx build --platform "$DOCKER_PLATFORM" --load \
  -t xhbx-rag-web:latest -f web/Dockerfile .

for image in \
  quay.io/coreos/etcd:v3.5.25 \
  minio/minio:RELEASE.2024-12-18T13-15-44Z \
  milvusdb/milvus:v2.6.19
do
  docker pull --platform "$DOCKER_PLATFORM" "$image"
done

for image in $IMAGES; do
  validate_image_platform "$image"
done

rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR/scripts"

cp docker-compose.offline.yml "$PACKAGE_DIR/docker-compose.offline.yml"
cp .env.offline.example "$PACKAGE_DIR/.env.offline.example"
cp scripts/deploy_web_offline.sh "$PACKAGE_DIR/deploy_web_offline.sh"
cp scripts/verify_web_offline.sh "$PACKAGE_DIR/verify_web_offline.sh"
cp scripts/index_parsed_offline.sh "$PACKAGE_DIR/scripts/index_parsed_offline.sh"
cp docs/Web问答界面离线部署文档.md "$PACKAGE_DIR/README.offline.md"
chmod +x \
  "$PACKAGE_DIR/deploy_web_offline.sh" \
  "$PACKAGE_DIR/verify_web_offline.sh" \
  "$PACKAGE_DIR/scripts/index_parsed_offline.sh"

copy_chunk_files "$CASE_LIST"
copy_chunk_files "$COURSE_LIST"

CASE_FILE_COUNT="$(wc -l < "$CASE_LIST" | tr -d '[:space:]')"
COURSE_FILE_COUNT="$(wc -l < "$COURSE_LIST" | tr -d '[:space:]')"
CASE_CHUNK_COUNT="$(count_lines "$CASE_LIST")"
COURSE_CHUNK_COUNT="$(count_lines "$COURSE_LIST")"

cat > "$PACKAGE_DIR/package-manifest.txt" <<EOF
package=$PACKAGE_NAME
platform=$DOCKER_PLATFORM
case_files=$CASE_FILE_COUNT
case_chunks=$CASE_CHUNK_COUNT
course_files=$COURSE_FILE_COUNT
course_chunks=$COURSE_CHUNK_COUNT
EOF
for image in $IMAGES; do
  printf 'image=%s\n' "$image" >> "$PACKAGE_DIR/package-manifest.txt"
done
append_data_checksums "$CASE_LIST"
append_data_checksums "$COURSE_LIST"

docker save -o "$PACKAGE_DIR/images.tar" $IMAGES
IMAGE_CHECKSUM="$(sha256_file "$PACKAGE_DIR/images.tar")"
printf '%s  images.tar\n' "$IMAGE_CHECKSUM" > "$PACKAGE_DIR/images.sha256"

tar -C "$OUTPUT_DIR" -czf "$TEMP_ARCHIVE" "$PACKAGE_NAME"
mv "$TEMP_ARCHIVE" "$FINAL_ARCHIVE"

echo "离线部署包已生成: $FINAL_ARCHIVE"
echo "目标平台: $DOCKER_PLATFORM"
echo "案例数据: ${CASE_FILE_COUNT} 个文件，${CASE_CHUNK_COUNT} 条 chunk"
echo "课程数据: ${COURSE_FILE_COUNT} 个文件，${COURSE_CHUNK_COUNT} 条 chunk"
