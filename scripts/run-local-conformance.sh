#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
set -a
source var/.env
set +a

curl --fail --silent --show-error http://127.0.0.1:8787/healthz >/dev/null
uv run python scripts/stt-conformance.py \
  --profiles large-v3-turbo-domain \
  --require-pass
RADCLIFFE_CONTROL_URL="${RADCLIFFE_CONTROL_URL:-http://127.0.0.1:8000}" \
  uv run python scripts/livekit-voice-smoke.py
