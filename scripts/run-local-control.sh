#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
set -a
source var/.env
set +a
export RADCLIFFE_BUILD_REVISION="$(git rev-parse HEAD)"
uv_bin="${RADCLIFFE_UV_BIN:-/opt/homebrew/bin/uv}"
exec "$uv_bin" run uvicorn radcliffe_control.app:app --host 0.0.0.0 --port 8000 --no-access-log
