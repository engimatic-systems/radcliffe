.PHONY: bootstrap check check-python check-node check-android test-python test-node

bootstrap:
	uv sync --all-packages --all-groups
	npm ci

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
