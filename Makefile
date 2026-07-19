.PHONY: bootstrap check check-python check-node check-android check-conformance-local \
	check-session-local check-voice-local check-stt-rejection-local \
	check-worker-dispatch-local check-device-auth-local check-operations-local \
	check-background-jobs-local build-job-sandbox-local \
	services-install-local services-status-local services-restart-local \
	backup-local check-recovery-local recovery-prune-plan-local \
	export-diagnostics-local audit-logs-local maintain-logs-local check-operations-tabletop \
	benchmark-stt-local benchmark-private-stt-local demo build-voice-dashboard \
	generate-protocol smoke-haiku-paid test-python test-node

bootstrap:
	uv sync --all-packages --all-groups
	npm ci

generate-protocol:
	./scripts/generate-protocol.sh

demo:
	uv run python scripts/foundation-demo.py

check: check-python check-node check-android

check-python:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy packages/domain-python/src services/control-plane/src services/media-worker/src
	uv run pytest

test-python:
	uv run pytest

check-node:
	npm run check
	npm test

test-node:
	npm test

smoke-haiku-paid:
	uv run python scripts/haiku_paid_smoke.py

check-android:
	JAVA_HOME="$${JAVA_HOME:-$$(brew --prefix openjdk@21)/libexec/openjdk.jdk/Contents/Home}" ANDROID_HOME="$${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools}" ./apps/android/gradlew -p apps/android testDebugUnitTest lint

check-voice-local:
	./scripts/run-local-conformance.sh

check-session-local:
	uv run python scripts/session-conformance.py

check-conformance-local: check-session-local check-stt-rejection-local check-voice-local

check-worker-dispatch-local:
	set -a; . var/.env; set +a; uv run python scripts/generated-session-smoke.py

check-device-auth-local:
	uv run python scripts/device-auth-conformance.py

check-operations-local:
	mkdir -p artifacts/operations
	scripts/run-local-ops.sh --wait 30 --output artifacts/operations/status.json

build-job-sandbox-local:
	scripts/build-job-sandbox.sh

check-background-jobs-local:
	set -a; . var/.env; . var/job-executor.env; set +a; \
		uv run python scripts/background_job_preflight.py

services-install-local:
	uv run python scripts/radcliffe_services.py install --include-compat-worker

services-status-local:
	uv run python scripts/radcliffe_services.py status --include-compat-worker

services-restart-local:
	uv run python scripts/radcliffe_services.py restart --include-compat-worker

backup-local:
	scripts/run-local-recovery.sh backup --retain 7

check-recovery-local:
	scripts/run-local-recovery.sh backup --kind drill --retain 7

recovery-prune-plan-local:
	scripts/run-local-recovery.sh prune --retain 7

export-diagnostics-local: check-operations-local
	scripts/run-local-diagnostics.sh

audit-logs-local: export-diagnostics-local
	scripts/run-local-log-maintenance.sh audit

maintain-logs-local:
	scripts/run-local-log-maintenance.sh maintain

check-operations-tabletop:
	uv run python scripts/operations-tabletop.py

check-stt-rejection-local:
	uv run python scripts/stt-rejection-conformance.py

build-voice-dashboard:
	uv run python scripts/build-voice-test-dashboard.py

benchmark-stt-local:
	uv run python scripts/stt-conformance.py --profiles all

SELECTED_STT_PROFILE ?= large-v3-turbo-domain

benchmark-private-stt-local:
	@test -n "$(CAPTURE_DIRECTORY)" || \
		{ echo "CAPTURE_DIRECTORY is required" >&2; exit 2; }
	uv run python scripts/stt_capture_benchmark.py \
		"$(CAPTURE_DIRECTORY)" --profiles all \
		--selected-profile "$(SELECTED_STT_PROFILE)" --require-pass
