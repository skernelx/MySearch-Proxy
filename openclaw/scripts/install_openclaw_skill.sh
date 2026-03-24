#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$SOURCE_DIR"
COPY_ENV=""

usage() {
  cat <<'EOF'
Usage: install_openclaw_skill.sh [options]

Prepare the bundled MySearch OpenClaw skill for local use.
This installer does not download remote runtime code and does not modify
other installed skills.

Options:
  --install-to DIR   Copy the skill bundle into DIR before finishing setup
  --copy-env FILE    Copy FILE to target .env with 0600 permissions
                     Optional. Prefer OpenClaw skill env injection instead.
  -h, --help         Show this help

Examples:
  bash scripts/install_openclaw_skill.sh
  bash scripts/install_openclaw_skill.sh --install-to ~/.openclaw/skills/mysearch
  bash scripts/install_openclaw_skill.sh --copy-env ~/.config/mysearch.env
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-to)
      TARGET_DIR="${2:?missing dir}"
      shift 2
      ;;
    --copy-env)
      COPY_ENV="${2:?missing env path}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

copy_skill_bundle() {
  if [[ "$TARGET_DIR" == "$SOURCE_DIR" ]]; then
    return
  fi

  mkdir -p "$TARGET_DIR"
  tar -C "$SOURCE_DIR" \
    --exclude='.env' \
    --exclude='__pycache__' \
    -cf - . | tar -C "$TARGET_DIR" -xf -
}

prepare_env_file() {
  if [[ "$SOURCE_DIR/.env.example" != "$TARGET_DIR/.env.example" ]]; then
    install -m 0644 "$SOURCE_DIR/.env.example" "$TARGET_DIR/.env.example"
  fi

  if [[ -n "$COPY_ENV" ]]; then
    install -m 0600 "$COPY_ENV" "$TARGET_DIR/.env"
  fi
}

copy_skill_bundle
prepare_env_file

cat <<EOF
MySearch OpenClaw skill is ready at: $TARGET_DIR

What changed:
1. Runtime is bundled inside the skill package
2. No remote downloads were performed
3. No other installed skills were modified

Next steps:
1. Prefer injecting env via OpenClaw skill config instead of copying secrets into the skill folder
2. Minimal trusted setup: MYSEARCH_PROXY_BASE_URL + MYSEARCH_PROXY_API_KEY
3. If you want Tavily to consume an upstream gateway, set MYSEARCH_TAVILY_MODE=gateway + MYSEARCH_TAVILY_GATEWAY_BASE_URL + MYSEARCH_TAVILY_GATEWAY_TOKEN
4. If you do not have a proxy yet, fall back to MYSEARCH_TAVILY_API_KEY + MYSEARCH_FIRECRAWL_API_KEY
5. Only use --copy-env or $TARGET_DIR/.env for local debugging
6. Run: python3 $TARGET_DIR/scripts/mysearch_openclaw.py health
EOF
