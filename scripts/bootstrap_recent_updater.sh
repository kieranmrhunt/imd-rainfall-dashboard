#!/usr/bin/env bash
set -euo pipefail

ROOT="${IMD_RECENT_HOME:-$HOME/mitre/imd-rainfall-dashboard}"
REPO="$ROOT/repo"
RUNTIME="$ROOT/runtime"
SEED_RUNTIME="$ROOT/seed-runtime"
GIT_URL="${IMD_GIT_URL:-git@github.com:kieranmrhunt/imd-rainfall-dashboard.git}"

mkdir -p "$ROOT" "$RUNTIME/realtime" "$RUNTIME/climatology" "$ROOT/logs"

if [[ ! -d "$REPO/.git" ]]; then
  if [[ -e "$REPO" ]]; then
    echo "error: $REPO exists but is not a Git checkout; move it aside before bootstrapping" >&2
    exit 2
  fi
  git clone "$GIT_URL" "$REPO"
fi

if [[ -d "$SEED_RUNTIME/realtime" ]]; then
  cp -an "$SEED_RUNTIME/realtime/." "$RUNTIME/realtime/"
fi
if [[ -d "$SEED_RUNTIME/climatology" ]]; then
  cp -an "$SEED_RUNTIME/climatology/." "$RUNTIME/climatology/"
fi

if [[ ! -x "$ROOT/venv/bin/python" ]]; then
  python3 -m venv "$ROOT/venv"
  "$ROOT/venv/bin/python" -m pip install --upgrade pip numpy
fi

chmod +x "$REPO/scripts/run_recent_update.sh"
IMD_RECENT_HOME="$ROOT" \
IMD_RECENT_RUNTIME_ROOT="$RUNTIME" \
  "$REPO/scripts/run_recent_update.sh"

cat <<EOF
Recent rainfall updater is ready in:
  $ROOT

Review $REPO/scripts/crontab.example, then install it with:
  crontab $REPO/scripts/crontab.example
EOF
