.PHONY: bootstrap check check-python check-node check-android demo generate-protocol test-python test-node

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
