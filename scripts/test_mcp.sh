#!/bin/sh
set -eu

MCP_URL="${MCP_URL:-http://127.0.0.1:${MCP_PORT:-9331}/mcp}"
MCP_PROTOCOL_VERSION="${MCP_PROTOCOL_VERSION:-2025-03-26}"
TIMEOUT="${TIMEOUT:-30}"
TOOL_PROFILE="${MCP_TOOL_PROFILE:-kb}"
PRIMARY_DOMAINS_JSON="${PRIMARY_DOMAINS_JSON:-[\"销售技能\"]}"
TOP_K="${TOP_K:-10}"
QUERY="${1:-${QUERY:-}}"
SESSION_ID=""

if ! command -v curl >/dev/null 2>&1; then
  echo "缺少 curl，请先安装 curl" >&2
  exit 1
fi

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

post_rpc() {
  label="$1"
  payload="$2"
  headers_file="$(mktemp)"
  body_file="$(mktemp)"

  echo "== $label =="
  if [ -n "$SESSION_ID" ]; then
    curl -fsS -N --max-time "$TIMEOUT" "$MCP_URL" \
      -D "$headers_file" \
      -o "$body_file" \
      -H "Content-Type: application/json" \
      -H "Accept: application/json, text/event-stream" \
      -H "mcp-protocol-version: $MCP_PROTOCOL_VERSION" \
      -H "mcp-session-id: $SESSION_ID" \
      -d "$payload"
  else
    curl -fsS -N --max-time "$TIMEOUT" "$MCP_URL" \
      -D "$headers_file" \
      -o "$body_file" \
      -H "Content-Type: application/json" \
      -H "Accept: application/json, text/event-stream" \
      -H "mcp-protocol-version: $MCP_PROTOCOL_VERSION" \
      -d "$payload"
  fi

  cat "$body_file"
  printf '\n\n'

  if [ -z "$SESSION_ID" ]; then
    SESSION_ID="$(
      awk 'BEGIN { IGNORECASE=1 } /^mcp-session-id:/ { gsub("\r", ""); print $2; exit }' "$headers_file"
    )"
    if [ -n "$SESSION_ID" ]; then
      echo "已获取 MCP session: $SESSION_ID"
      printf '\n'
    fi
  fi

  rm -f "$headers_file" "$body_file"
}

post_rpc "initialize" '{
  "jsonrpc":"2.0",
  "id":1,
  "method":"initialize",
  "params":{
    "protocolVersion":"2025-03-26",
    "capabilities":{},
    "clientInfo":{"name":"curl-test","version":"0.1.0"}
  }
}'

post_rpc "initialized notification" '{
  "jsonrpc":"2.0",
  "method":"notifications/initialized",
  "params":{}
}'

post_rpc "tools/list" '{
  "jsonrpc":"2.0",
  "id":2,
  "method":"tools/list",
  "params":{}
}'

case "$TOOL_PROFILE" in
  kb|both)
    :
    ;;
  legacy)
    post_rpc "retrieval_status" '{
      "jsonrpc":"2.0",
      "id":3,
      "method":"tools/call",
      "params":{
        "name":"retrieval_status",
        "arguments":{}
      }
    }'
    ;;
  *)
    echo "MCP_TOOL_PROFILE 仅支持 kb、legacy 或 both，当前为: $TOOL_PROFILE" >&2
    exit 1
    ;;
esac

if [ -n "$QUERY" ]; then
  escaped_query="$(json_escape "$QUERY")"
  case "$TOOL_PROFILE" in
    kb|both)
      post_rpc "kb_search_knowledge" "{
        \"jsonrpc\":\"2.0\",
        \"id\":3,
        \"method\":\"tools/call\",
        \"params\":{
          \"name\":\"kb_search_knowledge\",
          \"arguments\":{
            \"query\":\"$escaped_query\",
            \"primaryDomains\":$PRIMARY_DOMAINS_JSON,
            \"topK\":$TOP_K
          }
        }
      }"
      ;;
    legacy)
      post_rpc "search_knowledge" "{
        \"jsonrpc\":\"2.0\",
        \"id\":4,
        \"method\":\"tools/call\",
        \"params\":{
          \"name\":\"search_knowledge\",
          \"arguments\":{
            \"query\":\"$escaped_query\"
          }
        }
      }"
      ;;
  esac
else
  case "$TOOL_PROFILE" in
    legacy)
      echo "未提供检索问题，跳过 search_knowledge。"
      ;;
    *)
      echo "未提供检索问题，跳过 kb_search_knowledge。"
      ;;
  esac
  echo "如需检索测试：PRIMARY_DOMAINS_JSON='[\"销售技能\",\"客户经营\"]' QUERY='客户说预算不够怎么办？' $0"
  echo "全库检索测试：PRIMARY_DOMAINS_JSON='[]' QUERY='无法匹配现有体系的问题' $0"
fi
