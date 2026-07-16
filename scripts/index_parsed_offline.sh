#!/bin/sh
set -eu

PARSED_DIR="${PARSED_DIR:-parsed}"
COURSE_DIR="${COURSE_DIR:-$PARSED_DIR/chunk}"
TARGET="${1:-all}"
LIST_DIR="${TMPDIR:-/tmp}/xhbx-rag-offline-index.$$"
CASE_LIST="$LIST_DIR/case-files.txt"
COURSE_LIST="$LIST_DIR/course-files.txt"

case "$TARGET" in
  all|case|course) ;;
  *)
    echo "用法: $0 [all|case|course]" >&2
    exit 2
    ;;
esac

if [ ! -d "$PARSED_DIR" ]; then
  echo "parsed 目录不存在: $PARSED_DIR" >&2
  exit 1
fi

mkdir -p "$LIST_DIR"
trap 'rm -rf "$LIST_DIR"' EXIT HUP INT TERM

build_case_list() {
  : > "$CASE_LIST"
  for chunks_file in "$PARSED_DIR"/*/chunks.jsonl; do
    [ -f "$chunks_file" ] || continue
    case "$chunks_file" in
      "$COURSE_DIR"/*) continue ;;
    esac
    printf '%s\n' "$chunks_file" >> "$CASE_LIST"
  done
  LC_ALL=C sort -o "$CASE_LIST" "$CASE_LIST"
}

build_course_list() {
  : > "$COURSE_LIST"
  for chunks_file in "$COURSE_DIR"/*.chunks.jsonl; do
    [ -f "$chunks_file" ] || continue
    printf '%s\n' "$chunks_file" >> "$COURSE_LIST"
  done
  LC_ALL=C sort -o "$COURSE_LIST" "$COURSE_LIST"
}

index_list() {
  list_file="$1"
  collection="$2"
  label="$3"

  if [ ! -s "$list_file" ]; then
    echo "未找到${label} chunk 文件" >&2
    exit 1
  fi

  count=0
  while IFS= read -r chunks_file; do
    count=$((count + 1))
    mode="incremental"
    if [ "$count" -eq 1 ]; then
      mode="rebuild"
    fi
    echo "[${label} ${count}] 入库 ${chunks_file}，模式: ${mode}"
    xhbx-rag index \
      --chunks "$chunks_file" \
      --collection "$collection" \
      --mode "$mode"
  done < "$list_file"

  echo "${label}入库完成：${count} 个文件"
}

if [ "$TARGET" = "all" ] || [ "$TARGET" = "case" ]; then
  build_case_list
  index_list "$CASE_LIST" case "案例库"
fi

if [ "$TARGET" = "all" ] || [ "$TARGET" = "course" ]; then
  if [ ! -d "$COURSE_DIR" ]; then
    echo "课程 chunk 目录不存在: $COURSE_DIR" >&2
    exit 1
  fi
  build_course_list
  index_list "$COURSE_LIST" course "课程库"
fi
