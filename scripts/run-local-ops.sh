#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
if [[ ! -f var/.env ]]; then
  echo "Protected var/.env is required." >&2
  exit 1
fi
set -a
source var/.env
source scripts/load-local-network-env.sh
set +a
uv_bin="${RADCLIFFE_UV_BIN:-/opt/homebrew/bin/uv}"
exec "$uv_bin" run python scripts/radcliffe_ops.py "$@"
