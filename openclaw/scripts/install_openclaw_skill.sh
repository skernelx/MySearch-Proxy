#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$SOURCE_DIR"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
REPO_ROOT=""
REPO_OWNER="skernelx"
REPO_NAME="MySearch-Proxy"
REPO_REF="main"
REPLACE_SKILL=""
COPY_ENV=""

usage() {
  cat <<'EOF'
Usage: install_openclaw_skill.sh [options]

Bootstrap the MySearch OpenClaw skill runtime inside the current skill folder,
or copy the skill into another OpenClaw skills directory first.

Options:
  --install-to DIR        Install/copy the skill skeleton into DIR before bootstrap
  --repo-root DIR         Copy runtime files from a local MySearch-Proxy checkout
  --repo-owner NAME       GitHub owner for raw download (default: skernelx)
  --repo-name NAME        GitHub repo name (default: MySearch-Proxy)
  --repo-ref REF          GitHub ref to download (default: main)
  --replace-skill NAME    Backup and disable an old skill under ~/.openclaw/skills/NAME
  --copy-env FILE         Copy FILE to target .env
  -h, --help              Show this help

Examples:
  bash scripts/install_openclaw_skill.sh
  bash scripts/install_openclaw_skill.sh --replace-skill tavily
  bash scripts/install_openclaw_skill.sh --install-to ~/.openclaw/skills/mysearch --repo-root /path/to/MySearch-Proxy
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-to)
      TARGET_DIR="${2:?missing dir}"
      shift 2
      ;;
    --repo-root)
      REPO_ROOT="${2:?missing dir}"
      shift 2
      ;;
    --repo-owner)
      REPO_OWNER="${2:?missing owner}"
      shift 2
      ;;
    --repo-name)
      REPO_NAME="${2:?missing repo}"
      shift 2
      ;;
    --repo-ref)
      REPO_REF="${2:?missing ref}"
      shift 2
      ;;
    --replace-skill)
      REPLACE_SKILL="${2:?missing skill name}"
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

copy_skill_skeleton() {
  if [[ "$TARGET_DIR" == "$SOURCE_DIR" ]]; then
    return
  fi

  mkdir -p "$TARGET_DIR"
  tar -C "$SOURCE_DIR" \
    --exclude='.env' \
    --exclude='.venv' \
    --exclude='runtime' \
    --exclude='__pycache__' \
    -cf - SKILL.md .env.example scripts | tar -C "$TARGET_DIR" -xf -
}

backup_old_skill() {
  if [[ -z "$REPLACE_SKILL" ]]; then
    return
  fi

  local old_dir="$OPENCLAW_HOME/skills/$REPLACE_SKILL"
  local target_real=""
  local old_real=""

  target_real="$(python3 - <<'PY' "$TARGET_DIR"
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
)"

  if [[ ! -e "$old_dir" ]]; then
    return
  fi

  old_real="$(python3 - <<'PY' "$old_dir"
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
)"

  if [[ "$old_real" == "$target_real" ]]; then
    return
  fi

  local backup_root="$OPENCLAW_HOME/skills-disabled"
  local stamp
  stamp="$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$backup_root"
  mv "$old_dir" "$backup_root/${REPLACE_SKILL}-${stamp}"
  echo "Backed up old skill: $old_dir -> $backup_root/${REPLACE_SKILL}-${stamp}"
}

install_runtime_from_repo() {
  local runtime_dir="$TARGET_DIR/runtime/mysearch"
  local files=(__init__.py clients.py config.py keyring.py requirements.txt .env.example)

  rm -rf "$runtime_dir"
  mkdir -p "$runtime_dir"

  if [[ -n "$REPO_ROOT" ]]; then
    local source_pkg="$REPO_ROOT/mysearch"
    if [[ ! -d "$source_pkg" ]]; then
      echo "Local repo root does not contain mysearch/: $REPO_ROOT" >&2
      exit 1
    fi

    for file in "${files[@]}"; do
      install -m 0644 "$source_pkg/$file" "$runtime_dir/$file"
    done
    return
  fi

  local raw_base="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${REPO_REF}/mysearch"
  for file in "${files[@]}"; do
    curl -fsSL "${raw_base}/${file}" -o "$runtime_dir/$file"
  done
}

install_env_file() {
  if [[ "$SOURCE_DIR/.env.example" != "$TARGET_DIR/.env.example" ]]; then
    install -m 0644 "$SOURCE_DIR/.env.example" "$TARGET_DIR/.env.example"
  fi

  if [[ -n "$COPY_ENV" ]]; then
    install -m 0600 "$COPY_ENV" "$TARGET_DIR/.env"
    return
  fi

  if [[ -f "$SOURCE_DIR/.env" && ! -f "$TARGET_DIR/.env" ]]; then
    install -m 0600 "$SOURCE_DIR/.env" "$TARGET_DIR/.env"
  fi
}

install_venv() {
  local python_bin="$TARGET_DIR/.venv/bin/python"
  python3 -m venv "$TARGET_DIR/.venv"
  "$python_bin" -m pip install --upgrade pip >/dev/null
  # OpenClaw skill runtime only needs requests; the MCP server stack is not required here.
  "$python_bin" -m pip install requests==2.32.5
}

copy_skill_skeleton
backup_old_skill
install_runtime_from_repo
install_env_file
install_venv

cat <<EOF
MySearch OpenClaw skill is ready at: $TARGET_DIR

Next steps:
1. Edit $TARGET_DIR/.env if needed
2. Run: python3 $TARGET_DIR/scripts/mysearch_openclaw.py health
3. In OpenClaw, prefer this skill over legacy Tavily-only search
EOF
