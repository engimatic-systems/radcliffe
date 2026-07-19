#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
docker_bin="${RADCLIFFE_JOB_DOCKER_BIN:-/opt/homebrew/bin/docker}"
docker_host="${RADCLIFFE_JOB_DOCKER_HOST:-unix://${HOME}/.colima/default/docker.sock}"
revision="$(git rev-parse --short=12 HEAD)"
image="${1:-radcliffe-job-sandbox:${revision}}"

exec "$docker_bin" -H "$docker_host" build \
  --file infra/job-sandbox/Containerfile \
  --tag "$image" \
  .
