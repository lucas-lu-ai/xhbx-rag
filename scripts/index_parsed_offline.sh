#!/bin/sh
set -eu

PARSED_DIR="${PARSED_DIR:-parsed}"
NORMALIZED_DIR="${NORMALIZED_DIR:-/tmp/xhbx-parsed-normalized}"
COLLECTION_NAME="${COLLECTION_NAME:-xhbx_knowledge_chunks}"
BATCH_SIZE="${BATCH_SIZE:-64}"
TARGET="${1:-all}"

if [ "$TARGET" != "all" ]; then
  echo "用法: $0 all（统一 collection 不再分 case/course 入库）" >&2
  exit 2
fi

if [ ! -d "$PARSED_DIR" ]; then
  echo "parsed 目录不存在: $PARSED_DIR" >&2
  exit 1
fi

xhbx-rag normalize-knowledge \
  --input-dir "$PARSED_DIR" \
  --out "$NORMALIZED_DIR"

xhbx-rag index-dir \
  --chunks-dir "$NORMALIZED_DIR" \
  --collection-name "$COLLECTION_NAME" \
  --mode rebuild \
  --batch-size "$BATCH_SIZE"
