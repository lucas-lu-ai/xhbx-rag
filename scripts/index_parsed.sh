#!/bin/sh
set -eu

PARSED_DIR="${PARSED_DIR:-parsed}"
INDEX_MODE="${INDEX_MODE:-incremental}"
RESET_COLLECTION="${RESET_COLLECTION:-false}"
CHUNKS_LIST="/tmp/xhbx-rag-parsed-chunks.txt"

if [ ! -d "$PARSED_DIR" ]; then
  echo "parsed 目录不存在: $PARSED_DIR" >&2
  exit 1
fi

find "$PARSED_DIR" -type f -name "chunks.jsonl" | sort > "$CHUNKS_LIST"

if [ ! -s "$CHUNKS_LIST" ]; then
  echo "未在 $PARSED_DIR 下找到 chunks.jsonl" >&2
  exit 1
fi

count=0
while IFS= read -r chunks_file; do
  count=$((count + 1))
  current_mode="$INDEX_MODE"
  if [ "$RESET_COLLECTION" = "true" ] && [ "$count" -eq 1 ]; then
    current_mode="rebuild"
  fi
  echo "[${count}] 入库 ${chunks_file}，模式: ${current_mode}"
  xhbx-rag index --chunks "$chunks_file" --mode "$current_mode"
done < "$CHUNKS_LIST"

echo "完成：已处理 ${count} 个 chunks.jsonl，默认模式: ${INDEX_MODE}，重建开关: ${RESET_COLLECTION}"
