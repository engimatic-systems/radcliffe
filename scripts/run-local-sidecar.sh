#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
set -a
source var/.env
set +a
if [[ -n "${RADCLIFFE_OPENAI_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
  export OPENAI_API_KEY="$RADCLIFFE_OPENAI_API_KEY"
fi
node_bin="${RADCLIFFE_NODE_BIN:-$HOME/.local/bin/node}"
exec "$node_bin" services/agent-sidecar/dist/src/server.js
