#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
set -a
source var/.env
set +a
unset OPENAI_API_KEY ANTHROPIC_API_KEY
if [[ "${RADCLIFFE_ALLOW_CLOUD_MODELS:-0}" == "1" ]]; then
  case "${RADCLIFFE_MODEL_PROVIDER:-ollama}" in
    openai)
      export OPENAI_API_KEY="${RADCLIFFE_OPENAI_API_KEY:-}"
      ;;
    anthropic)
      export ANTHROPIC_API_KEY="${RADCLIFFE_ANTHROPIC_API_KEY:-}"
      ;;
  esac
fi
node_bin="${RADCLIFFE_NODE_BIN:-$HOME/.local/bin/node}"
exec "$node_bin" services/agent-sidecar/dist/src/server.js
