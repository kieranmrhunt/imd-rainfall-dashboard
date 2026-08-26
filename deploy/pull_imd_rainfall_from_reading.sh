#!/usr/bin/env bash
set -euo pipefail

SOURCE_URL="${IMD_SOURCE_URL:-https://www.met.reading.ac.uk/~rz908899/imd-rainfall/latest.php}"
DEST_DIR="${IMD_DEST_DIR:-$HOME/incompass/public/kieran/imd-rainfall}"
DEST_FILE="$DEST_DIR/latest.json"

mkdir -p "$DEST_DIR"
temporary="$(mktemp "$DEST_DIR/.latest.json.XXXXXX")"
trap 'rm -f "$temporary"' EXIT

curl --fail --silent --show-error --location --max-time 120 "$SOURCE_URL" --output "$temporary"
python3 -c 'import json, pathlib, sys; data=json.loads(pathlib.Path(sys.argv[1]).read_text()); assert data.get("meta", {}).get("latestAvailableDate"); assert data.get("daily"); assert data.get("periods")' "$temporary"
chmod 0644 "$temporary"
mv -f "$temporary" "$DEST_FILE"
trap - EXIT
