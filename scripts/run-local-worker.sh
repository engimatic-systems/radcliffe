#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
set -a
source var/.env
set +a

if [[ -z "${RADCLIFFE_OPENAI_API_KEY:-}" ]]; then
  echo "Add RADCLIFFE_OPENAI_API_KEY to var/.env first." >&2
  exit 1
fi

session_id="${RADCLIFFE_SESSION_ID:-radcliffe-demo}"
uv_bin="${RADCLIFFE_UV_BIN:-/opt/homebrew/bin/uv}"
response="$(curl --fail --silent --show-error \
  -X POST http://127.0.0.1:8000/v1/sessions \
  -H "Authorization: Bearer ${RADCLIFFE_BOOTSTRAP_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"${session_id}\",\"participant_identity\":\"radcliffe-worker\",\"role\":\"worker\"}")"

export RADCLIFFE_SESSION_ID="$session_id"
export RADCLIFFE_LIVEKIT_TOKEN
RADCLIFFE_LIVEKIT_TOKEN="$(printf '%s' "$response" | "$uv_bin" run python -c \
  'import json,sys; print(json.load(sys.stdin)["participant_token"])')"

exec "$uv_bin" run python -m radcliffe_media.production
