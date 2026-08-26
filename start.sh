#!/usr/bin/env bash
set -euo pipefail
CALLER_DIR="$PWD"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export UIPM_DATA_DIR="${UIPM_DATA_DIR:-$CALLER_DIR/data}"
if [[ -z "${UIPM_ACCESS_KEY:-}" ]]; then
  echo "ERROR: UIPM_ACCESS_KEY is required." >&2
  echo "Example: UIPM_ACCESS_KEY='your-secret-key' $0" >&2
  exit 2
fi
cd "$SCRIPT_DIR"
exec uv run python -m app.main
