#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
if [[ ! -f var/.env ]]; then
  echo "protected var/.env is required" >&2
  exit 1
fi
set -a
source var/.env
set +a
exec uv run python scripts/radcliffe_telemetry.py "$@"
