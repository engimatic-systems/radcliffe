#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
set -a
source var/.env
set +a
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH}"

session_id="${RADCLIFFE_SESSION_ID:-radcliffe-demo}"
uv_bin="${RADCLIFFE_UV_BIN:-/opt/homebrew/bin/uv}"
response="$(curl --fail --silent --show-error \
  -X POST "http://127.0.0.1:8000/v1/internal/sessions/${session_id}/worker-credentials" \
  -H "Authorization: Bearer ${RADCLIFFE_INTERNAL_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{}')"

export RADCLIFFE_SESSION_ID="$session_id"
export RADCLIFFE_LIVEKIT_TOKEN
RADCLIFFE_LIVEKIT_TOKEN="$(printf '%s' "$response" | "$uv_bin" run python -c \
  'import json,sys; print(json.load(sys.stdin)["participant_token"])')"

exec "$uv_bin" run python -m radcliffe_media.production
