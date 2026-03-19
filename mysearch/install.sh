#!/usr/bin/env bash
set -euo pipefail

MYSEARCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$MYSEARCH_DIR/.." && pwd)"

exec "$ROOT_DIR/install.sh"
