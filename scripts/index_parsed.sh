#!/bin/sh
set -eu

PARSED_DIR="${PARSED_DIR:-parsed}"
NORMALIZED_DIR="${NORMALIZED_DIR:-parsed_normalized}"
COLLECTION_NAME="${COLLECTION_NAME:-xhbx_knowledge_chunks}"
BATCH_SIZE="${BATCH_SIZE:-64}"

if [ ! -d "$PARSED_DIR" ]; then
  echo "parsed 目录不存在: $PARSED_DIR" >&2
  exit 1
fi

uv run xhbx-rag normalize-knowledge \
  --input-dir "$PARSED_DIR" \
  --out "$NORMALIZED_DIR"

uv run xhbx-rag index-dir \
  --chunks-dir "$NORMALIZED_DIR" \
  --collection-name "$COLLECTION_NAME" \
  --mode rebuild \
  --batch-size "$BATCH_SIZE"

echo "统一知识库入库完成：collection=${COLLECTION_NAME}"
