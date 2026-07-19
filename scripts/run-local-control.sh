#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
set -a
source var/.env
if [[ -f var/job-executor.env ]]; then
  mode="$(stat -f '%Lp' var/job-executor.env 2>/dev/null || stat -c '%a' var/job-executor.env)"
  if [[ "$mode" != "600" ]]; then
    echo "Radcliffe job executor environment must have mode 0600" >&2
    exit 1
  fi
  source var/job-executor.env
fi
set +a
export RADCLIFFE_BUILD_REVISION="$(git rev-parse HEAD)"
uv_bin="${RADCLIFFE_UV_BIN:-/opt/homebrew/bin/uv}"
exec "$uv_bin" run uvicorn radcliffe_control.app:app --host 0.0.0.0 --port 8000 --no-access-log
