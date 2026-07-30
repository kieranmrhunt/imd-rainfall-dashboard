#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
RUNTIME_ROOT="${IMD_RECENT_RUNTIME_ROOT:-$REPO_DIR/runtime}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ -x "${IMD_RECENT_HOME:-}/venv/bin/python" ]]; then
  PYTHON_BIN="${IMD_RECENT_HOME}/venv/bin/python"
fi

cd "$REPO_DIR"
if [[ ! -d .git ]]; then
  echo "error: $REPO_DIR is not a Git checkout; run bootstrap_recent_updater.sh first" >&2
  exit 2
fi

git pull --rebase origin main

"$PYTHON_BIN" scripts/update_recent_rainfall.py \
  --cache-dir "$RUNTIME_ROOT/realtime" \
  --climatology-dir "$RUNTIME_ROOT/climatology" \
  --output data/recent_data.js \
  --manifest data/recent_manifest.json

if git diff --quiet -- data/recent_data.js data/recent_manifest.json; then
  echo "recent rainfall is already current"
  exit 0
fi

git add data/recent_data.js data/recent_manifest.json
if git diff --cached --quiet; then
  echo "recent rainfall is already current"
  exit 0
fi

LATEST="$("$PYTHON_BIN" -c 'import json; print(json.load(open("data/recent_manifest.json", encoding="utf-8"))["latestAvailableDate"])')"
git -c user.name="${GIT_AUTHOR_NAME:-IMD rainfall updater}" \
  -c user.email="${GIT_AUTHOR_EMAIL:-kieranmrhunt@users.noreply.github.com}" \
  commit -m "data: update recent IMD rainfall through $LATEST"
git push origin HEAD:main
