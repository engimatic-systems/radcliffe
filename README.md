# Radcliffe

Radcliffe is a local-first Android voice interface for tool-using frontier
models with deterministic, provenance-preserving source reading.

The first milestone is being developed on `epic/radcliffe-foundation`. Its
acceptance demo is defined in [the product and technical specification](docs/radcliffe-spec.md#milestone-1-verifiable-foundation-demo).

## Repository layout

- `apps/android/` — native Kotlin/Compose client
- `packages/domain-python/` — protocol-independent domain and persistence code
- `packages/protocol/` — versioned Protobuf contract
- `services/control-plane/` — authenticated HTTP/token service
- `services/media-worker/` — LiveKit audio and speech pipeline
- `services/agent-sidecar/` — Pi Agent Core adapter
- `infra/livekit/` — self-hosted media topology
- `tests/conformance/` — external audio-first acceptance suite

## Bootstrap

Requirements: `uv`, Node 22.22+, npm, and a JDK supported by Android Gradle
Plugin 9.2. Android builds additionally require Android SDK 36.

```sh
make bootstrap
make check
```

Individual checks:

```sh
make check-python
make check-node
make check-android
```

Secrets are configured through environment files copied from committed
`.env.example` templates. Never commit populated `.env` files.
