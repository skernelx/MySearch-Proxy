#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${MYSEARCH_PROXY_API_KEY:-}" && -n "${MYSEARCH_PROXY_BOOTSTRAP_TOKEN:-}" ]]; then
  export MYSEARCH_PROXY_API_KEY="$(
    python /app/mysearch/scripts/bootstrap_proxy_token.py
  )"
fi

exec "$@"
