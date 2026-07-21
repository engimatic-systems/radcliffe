#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
set -a
source var/.env
source scripts/load-local-network-env.sh
set +a
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH}"

session_id="${RADCLIFFE_SESSION_ID:-radcliffe-demo}"
uv_bin="${RADCLIFFE_UV_BIN:-/opt/homebrew/bin/uv}"
response=""
for attempt in {1..30}; do
  if response="$(curl --fail --silent \
    -X POST "http://127.0.0.1:8000/v1/internal/sessions/${session_id}/worker-credentials" \
    -H "Authorization: Bearer ${RADCLIFFE_INTERNAL_TOKEN}" \
    -H 'Content-Type: application/json' \
    -d '{}' 2>/dev/null)"; then
    break
  fi
  if [[ "$attempt" == "30" ]]; then
    echo "control plane unavailable after worker startup deadline" >&2
    exit 1
  fi
  sleep 1
done

export RADCLIFFE_SESSION_ID="$session_id"
export RADCLIFFE_LIVEKIT_TOKEN
RADCLIFFE_LIVEKIT_TOKEN="$(printf '%s' "$response" | "$uv_bin" run python -c \
  'import json,sys; print(json.load(sys.stdin)["participant_token"])')"

exec "$uv_bin" run python -m radcliffe_media.production
