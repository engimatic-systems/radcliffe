.PHONY: bootstrap check check-python check-node check-android check-conformance-local \
	check-session-local check-voice-local check-stt-rejection-local \
	check-worker-dispatch-local check-device-auth-local check-operations-local \
	services-install-local services-status-local services-restart-local \
	benchmark-stt-local demo build-voice-dashboard \
	generate-protocol test-python test-node

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

services-install-local:
	uv run python scripts/radcliffe_services.py install --include-compat-worker

services-status-local:
	uv run python scripts/radcliffe_services.py status --include-compat-worker

services-restart-local:
	uv run python scripts/radcliffe_services.py restart --include-compat-worker

check-stt-rejection-local:
	uv run python scripts/stt-rejection-conformance.py

build-voice-dashboard:
	uv run python scripts/build-voice-test-dashboard.py

benchmark-stt-local:
	uv run python scripts/stt-conformance.py --profiles all
