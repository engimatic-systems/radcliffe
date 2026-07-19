#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
profile="${1:-}"
env_file="${RADCLIFFE_ENV_FILE:-var/.env}"

if [[ ! -f "$env_file" ]]; then
  echo "Radcliffe environment file is missing" >&2
  exit 1
fi
mode="$(stat -f '%Lp' "$env_file" 2>/dev/null || stat -c '%a' "$env_file")"
if [[ "$mode" != "600" ]]; then
  echo "Radcliffe environment file must have mode 0600" >&2
  exit 1
fi

set -a
source "$env_file"
set +a
export RADCLIFFE_BUILD_REVISION="$(git rev-parse HEAD)"

case "$profile" in
  local-ollama)
    export RADCLIFFE_MODEL_PROVIDER=ollama
    export RADCLIFFE_MODEL_ID="${RADCLIFFE_LOCAL_MODEL_ID:-qwen3:8b}"
    export RADCLIFFE_MODEL_CONTEXT_LIMIT="${RADCLIFFE_LOCAL_MODEL_CONTEXT_LIMIT:-40960}"
    export RADCLIFFE_MODEL_RESERVED_OUTPUT_TOKENS="${RADCLIFFE_LOCAL_MODEL_RESERVED_OUTPUT_TOKENS:-8192}"
    unset ANTHROPIC_API_KEY OPENAI_API_KEY
    unset RADCLIFFE_ANTHROPIC_API_KEY RADCLIFFE_OPENAI_API_KEY
    ;;
  live-haiku-4.5)
    if [[ "${RADCLIFFE_ALLOW_CLOUD_MODELS:-0}" != "1" ]]; then
      echo "Cloud model profile requires RADCLIFFE_ALLOW_CLOUD_MODELS=1 for this approved run" >&2
      exit 1
    fi
    if [[ -z "${RADCLIFFE_ANTHROPIC_API_KEY:-}" ]]; then
      echo "Live Haiku profile requires the protected Radcliffe Anthropic key" >&2
      exit 1
    fi
    export RADCLIFFE_MODEL_PROVIDER=anthropic
    export RADCLIFFE_MODEL_ID=claude-haiku-4-5-20251001
    export ANTHROPIC_API_KEY="$RADCLIFFE_ANTHROPIC_API_KEY"
    unset RADCLIFFE_ANTHROPIC_API_KEY OPENAI_API_KEY RADCLIFFE_OPENAI_API_KEY
    echo "Cloud profile active: conversation context is sent to Anthropic; no local fallback is automatic." >&2
    ;;
  *)
    echo "Unknown Radcliffe model profile" >&2
    exit 1
    ;;
esac

node_bin="${RADCLIFFE_NODE_BIN:-$HOME/.local/bin/node}"
exec "$node_bin" services/agent-sidecar/dist/src/server.js
