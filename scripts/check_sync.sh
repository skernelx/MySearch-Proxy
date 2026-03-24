#!/usr/bin/env bash
# Verify mysearch/ runtime files are in sync with openclaw/runtime/mysearch/
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$BASE/mysearch"
DST="$BASE/openclaw/runtime/mysearch"

FILES=(clients.py config.py keyring.py __init__.py)
EXIT=0

for f in "${FILES[@]}"; do
    if [[ ! -f "$SRC/$f" ]]; then
        continue
    fi
    if [[ ! -f "$DST/$f" ]]; then
        echo "MISSING: $DST/$f"
        EXIT=1
        continue
    fi
    if ! diff -q "$SRC/$f" "$DST/$f" >/dev/null 2>&1; then
        echo "DESYNC: $f"
        EXIT=1
    fi
done

if [[ $EXIT -eq 0 ]]; then
    echo "OK: all runtime files in sync"
else
    echo ""
    echo "Fix: cp mysearch/{clients,config,keyring,__init__}.py openclaw/runtime/mysearch/"
    exit 1
fi
