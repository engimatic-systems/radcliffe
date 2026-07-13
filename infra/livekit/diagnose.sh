#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
set -a
source .env
set +a

status=0
check() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS $label"
  else
    echo "FAIL $label"
    status=1
  fi
}

check "LiveKit DNS" dig +short "$LIVEKIT_DOMAIN"
check "TURN DNS" dig +short "$TURN_DOMAIN"
check "signal TLS and health" curl --fail --silent --show-error "https://$LIVEKIT_DOMAIN"
check "TURN trusted TLS" openssl s_client -connect "$TURN_DOMAIN:443" -servername "$TURN_DOMAIN" -verify_return_error </dev/null
check "ICE/TCP reachable" nc -z -w 5 "$LIVEKIT_DOMAIN" 7881

exit "$status"
