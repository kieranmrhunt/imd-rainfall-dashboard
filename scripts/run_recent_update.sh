#!/usr/bin/env bash
set -euo pipefail

ROOT="${IMD_RECENT_HOME:-$HOME/mitre/imd-rainfall-dashboard}"
REPO_DIR="${IMD_REPO_DIR:-$ROOT/repo}"
RUNTIME_ROOT="${IMD_RECENT_RUNTIME_ROOT:-$ROOT/runtime}"
STATE_DIR="${IMD_RECENT_STATE_DIR:-$ROOT/state}"
PUBLISH_DIR="${IMD_PUBLISH_DIR:-/storage/silver/metweb/$USER/public_html/imd-rainfall}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ -x "$ROOT/venv/bin/python" ]] && "$ROOT/venv/bin/python" -c 'import numpy' >/dev/null 2>&1; then
  PYTHON_BIN="$ROOT/venv/bin/python"
fi
if ! "$PYTHON_BIN" -c 'import numpy' >/dev/null 2>&1; then
  echo "error: $PYTHON_BIN cannot import NumPy" >&2
  exit 2
fi
if [[ ! -f "$REPO_DIR/scripts/update_recent_rainfall.py" ]]; then
  echo "error: updater not found under $REPO_DIR" >&2
  exit 2
fi

mkdir -p "$RUNTIME_ROOT/realtime" "$RUNTIME_ROOT/climatology" "$STATE_DIR" "$PUBLISH_DIR"
cd "$REPO_DIR"

"$PYTHON_BIN" scripts/update_recent_rainfall.py \
  --cache-dir "$RUNTIME_ROOT/realtime" \
  --climatology-dir "$RUNTIME_ROOT/climatology" \
  --output "$STATE_DIR/recent_data.js" \
  --json-output "$PUBLISH_DIR/latest.json" \
  --manifest "$STATE_DIR/recent_manifest.json"

chmod 0644 "$PUBLISH_DIR/latest.json"
