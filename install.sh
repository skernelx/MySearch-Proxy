#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT_DIR/venv/bin/python"

if [[ -x "$VENV_PY" ]]; then
  PYTHON_BIN="$VENV_PY"
else
  PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
fi

echo "Installing MySearch MCP dependencies..."
"$PYTHON_BIN" -m pip install -r "$ROOT_DIR/mysearch/requirements.txt"

ENV_KEYS=(
  MYSEARCH_NAME
  MYSEARCH_TIMEOUT_SECONDS
  MYSEARCH_PROXY_BASE_URL
  MYSEARCH_PROXY_API_KEY
  MYSEARCH_MCP_HOST
  MYSEARCH_MCP_PORT
  MYSEARCH_MCP_MOUNT_PATH
  MYSEARCH_MCP_SSE_PATH
  MYSEARCH_MCP_STREAMABLE_HTTP_PATH
  MYSEARCH_MCP_STATELESS_HTTP
  MYSEARCH_TAVILY_BASE_URL
  MYSEARCH_TAVILY_SEARCH_PATH
  MYSEARCH_TAVILY_EXTRACT_PATH
  MYSEARCH_TAVILY_AUTH_MODE
  MYSEARCH_TAVILY_AUTH_HEADER
  MYSEARCH_TAVILY_AUTH_SCHEME
  MYSEARCH_TAVILY_AUTH_FIELD
  MYSEARCH_TAVILY_API_KEY
  MYSEARCH_TAVILY_API_KEYS
  MYSEARCH_TAVILY_KEYS_FILE
  MYSEARCH_TAVILY_ACCOUNTS_FILE
  MYSEARCH_FIRECRAWL_BASE_URL
  MYSEARCH_FIRECRAWL_SEARCH_PATH
  MYSEARCH_FIRECRAWL_SCRAPE_PATH
  MYSEARCH_FIRECRAWL_AUTH_MODE
  MYSEARCH_FIRECRAWL_AUTH_HEADER
  MYSEARCH_FIRECRAWL_AUTH_SCHEME
  MYSEARCH_FIRECRAWL_AUTH_FIELD
  MYSEARCH_FIRECRAWL_API_KEY
  MYSEARCH_FIRECRAWL_API_KEYS
  MYSEARCH_FIRECRAWL_KEYS_FILE
  MYSEARCH_FIRECRAWL_ACCOUNTS_FILE
  MYSEARCH_EXA_BASE_URL
  MYSEARCH_EXA_SEARCH_PATH
  MYSEARCH_EXA_AUTH_MODE
  MYSEARCH_EXA_AUTH_HEADER
  MYSEARCH_EXA_AUTH_SCHEME
  MYSEARCH_EXA_AUTH_FIELD
  MYSEARCH_EXA_API_KEY
  MYSEARCH_EXA_API_KEYS
  MYSEARCH_EXA_KEYS_FILE
  MYSEARCH_EXA_ACCOUNTS_FILE
  MYSEARCH_XAI_BASE_URL
  MYSEARCH_XAI_RESPONSES_PATH
  MYSEARCH_XAI_SOCIAL_BASE_URL
  MYSEARCH_XAI_SOCIAL_SEARCH_PATH
  MYSEARCH_XAI_SEARCH_MODE
  MYSEARCH_XAI_AUTH_MODE
  MYSEARCH_XAI_AUTH_HEADER
  MYSEARCH_XAI_AUTH_SCHEME
  MYSEARCH_XAI_AUTH_FIELD
  MYSEARCH_XAI_API_KEY
  MYSEARCH_XAI_API_KEYS
  MYSEARCH_XAI_KEYS_FILE
  MYSEARCH_XAI_MODEL
)

load_existing_codex_mysearch_env() {
  local config_path="${CODEX_HOME:-$HOME/.codex}/config.toml"
  [[ -f "$config_path" ]] || return 0

  while IFS='=' read -r key value; do
    if [[ -z "${!key-}" ]]; then
      export "$key=$value"
    fi
  done < <(
    "$PYTHON_BIN" - <<'PY' "$config_path" "${ENV_KEYS[@]}"
from pathlib import Path
import sys

config_path = Path(sys.argv[1])
keys = set(sys.argv[2:])
config_text = config_path.read_text(encoding="utf-8")

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:
    tomllib = None  # type: ignore[assignment]

env = {}
if tomllib is not None:
    try:
        data = tomllib.loads(config_text)
        env = ((data.get("mcp_servers") or {}).get("mysearch") or {}).get("env") or {}
    except Exception:
        env = {}

if not isinstance(env, dict) or not env:
    env = {}
    in_section = False
    for raw_line in config_text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            in_section = line == "[mcp_servers.mysearch.env]"
            continue
        if not in_section or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        if key and value:
            env[key] = value

for key, value in env.items():
    if key in keys and isinstance(value, str) and value.strip():
        print(f"{key}={value}")
PY
  )
}

load_env_file_defaults() {
  local env_path="${1:?missing env path}"
  [[ -f "$env_path" ]] || return 0

  while IFS='=' read -r key value; do
    if [[ -z "${!key-}" ]]; then
      export "$key=$value"
    fi
  done < <(
    "$PYTHON_BIN" - <<'PY' "$env_path"
from pathlib import Path
import sys

for raw_line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
        value = value[1:-1]
    print(f"{key}={value}")
PY
  )
}

load_existing_codex_mysearch_env
load_env_file_defaults "$ROOT_DIR/mysearch/.env"

CLAUDE_ENV_ARGS=(-e "PYTHONPATH=$ROOT_DIR")
CODEX_ENV_ARGS=(--env "PYTHONPATH=$ROOT_DIR")

for key in "${ENV_KEYS[@]}"; do
  value="${!key-}"
  if [[ -n "${value}" ]]; then
    CLAUDE_ENV_ARGS+=(-e "$key=$value")
    CODEX_ENV_ARGS+=(--env "$key=$value")
  fi
done

registered_targets=()

if command -v claude >/dev/null 2>&1; then
  echo "Registering MySearch in Claude Code..."
  claude mcp remove mysearch >/dev/null 2>&1 || true
  claude mcp add mysearch \
    "${CLAUDE_ENV_ARGS[@]}" \
    -- "$PYTHON_BIN" -m mysearch
  registered_targets+=("Claude Code")
fi

if command -v codex >/dev/null 2>&1; then
  echo "Registering MySearch in Codex..."
  codex mcp remove mysearch >/dev/null 2>&1 || true
  codex mcp add mysearch \
    "${CODEX_ENV_ARGS[@]}" \
    -- "$PYTHON_BIN" -m mysearch
  registered_targets+=("Codex")
fi

echo
if [[ ${#registered_targets[@]} -eq 0 ]]; then
  echo "Dependencies are installed, but neither 'claude' nor 'codex' was found in PATH."
  echo "You can register manually with:"
  echo "  claude mcp add mysearch -e PYTHONPATH=$ROOT_DIR -- $PYTHON_BIN -m mysearch"
  echo "  codex mcp add mysearch --env PYTHONPATH=$ROOT_DIR -- $PYTHON_BIN -m mysearch"
  exit 0
fi

echo "MySearch is ready."
printf 'Registered in: %s\n' "${registered_targets[*]}"
if command -v claude >/dev/null 2>&1; then
  echo "Check Claude Code with: claude mcp list"
fi
if command -v codex >/dev/null 2>&1; then
  echo "Check Codex with: codex mcp list"
fi
