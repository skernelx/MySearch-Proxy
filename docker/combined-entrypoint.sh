#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH="${PYTHONPATH:-/app}:/app/proxy"
export MYSEARCH_PROXY_BASE_URL="${MYSEARCH_PROXY_BASE_URL:-http://127.0.0.1:9874}"
export MYSEARCH_PROXY_HOST="${MYSEARCH_PROXY_HOST:-0.0.0.0}"

cleanup() {
  local exit_code=$?
  if [[ -n "${MCP_PID:-}" ]]; then
    kill "${MCP_PID}" 2>/dev/null || true
  fi
  if [[ -n "${PROXY_PID:-}" ]]; then
    kill "${PROXY_PID}" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
  exit "${exit_code}"
}

trap cleanup EXIT INT TERM

python -m uvicorn proxy.server:app --host "${MYSEARCH_PROXY_HOST}" --port 9874 &
PROXY_PID=$!

if [[ -z "${MYSEARCH_PROXY_API_KEY:-}" && -n "${MYSEARCH_PROXY_BOOTSTRAP_TOKEN:-}" ]]; then
  export MYSEARCH_PROXY_API_KEY="$(
    python /app/mysearch/scripts/bootstrap_proxy_token.py
  )"
fi

python -m mysearch --transport streamable-http --host 0.0.0.0 --port "${MYSEARCH_MCP_PORT:-8000}" &
MCP_PID=$!

wait -n "${PROXY_PID}" "${MCP_PID}"
