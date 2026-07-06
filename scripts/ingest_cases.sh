#!/usr/bin/env bash
set -uo pipefail

base_dir="data/绩优案例"
list_file=""
continue_on_error=0
dry_run=0
cases=()

usage() {
  cat <<'USAGE'
用法:
  scripts/ingest_cases.sh --list cases.txt
  scripts/ingest_cases.sh "案例A" "案例 B"

选项:
  --base-dir DIR          案例根目录，默认: data/绩优案例
  --list FILE             从文件读取案例名称；空行和 # 开头的行会被忽略
  --continue-on-error     单个案例失败后继续处理后续案例
  --dry-run               只打印将执行的命令，不真正执行
  -h, --help              显示帮助

列表文件示例:
  # cases.txt
  【林洁玉】解读“国十条”走进高端
  案例 B 含空格
USAGE
}

die() {
  printf '错误: %s\n' "$*" >&2
  exit 2
}

trim() {
  local value=$1
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

append_cases_from_file() {
  local file=$1
  local line trimmed

  [[ -f "$file" ]] || die "列表文件不存在: $file"

  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line%$'\r'}
    trimmed=$(trim "$line")

    [[ -z "$trimmed" ]] && continue
    [[ "${trimmed:0:1}" == "#" ]] && continue

    cases+=("$trimmed")
  done < "$file"
}

print_command() {
  local arg

  printf '执行:'
  for arg in "$@"; do
    printf " '%s'" "$arg"
  done
  printf '\n'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-dir)
      [[ $# -ge 2 ]] || die "--base-dir 需要一个目录参数"
      base_dir=$2
      shift 2
      ;;
    --list)
      [[ $# -ge 2 ]] || die "--list 需要一个文件参数"
      list_file=$2
      shift 2
      ;;
    --continue-on-error)
      continue_on_error=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        cases+=("$1")
        shift
      done
      ;;
    -*)
      die "未知选项: $1"
      ;;
    *)
      cases+=("$1")
      shift
      ;;
  esac
done

if [[ -n "$list_file" ]]; then
  append_cases_from_file "$list_file"
fi

[[ ${#cases[@]} -gt 0 ]] || {
  usage >&2
  die "请通过 --list 或命令行参数提供至少一个案例名称"
}

failures=0
total=${#cases[@]}

for index in "${!cases[@]}"; do
  case_name=${cases[$index]}
  case_dir="${base_dir%/}/$case_name"

  printf '\n[%d/%d] %s\n' "$((index + 1))" "$total" "$case_dir"

  if [[ ! -d "$case_dir" ]]; then
    printf '错误: 案例目录不存在: %s\n' "$case_dir" >&2
    failures=$((failures + 1))
    [[ $continue_on_error -eq 1 ]] || exit 1
    continue
  fi

  cmd=(
    uv run xhbx-rag ingest
    --case-dir "$case_dir"
    --stream
    --reuse-section-evidence
    --no-thinking
    --trace
  )

  print_command "${cmd[@]}"

  if [[ $dry_run -eq 1 ]]; then
    continue
  fi

  "${cmd[@]}"
  status=$?
  if [[ $status -ne 0 ]]; then
    printf '错误: 案例执行失败，退出码 %d: %s\n' "$status" "$case_dir" >&2
    failures=$((failures + 1))
    [[ $continue_on_error -eq 1 ]] || exit "$status"
  fi
done

if [[ $failures -gt 0 ]]; then
  printf '\n完成，但有 %d 个案例失败。\n' "$failures" >&2
  exit 1
fi

printf '\n全部 %d 个案例执行完成。\n' "$total"
