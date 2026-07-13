#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
if [[ ! -f .env ]]; then
  echo "Copy .env.example to .env and set deployment secrets first." >&2
  exit 1
fi
set -a
source .env
set +a

for variable in LIVEKIT_API_KEY LIVEKIT_API_SECRET TURN_DOMAIN TURN_CERT_FILE TURN_KEY_FILE; do
  if [[ -z "${!variable:-}" ]]; then
    echo "$variable is required" >&2
    exit 1
  fi
done
if (( ${#LIVEKIT_API_SECRET} < 32 )); then
  echo "LIVEKIT_API_SECRET must contain at least 32 characters" >&2
  exit 1
fi

envsubst < livekit.yaml.template > livekit.yaml
chmod 600 livekit.yaml
echo "Wrote livekit.yaml"
