# Radcliffe

Radcliffe is a local-first Android voice interface for tool-using frontier
models with deterministic, provenance-preserving source reading.

The first milestone is implemented on `epic/radcliffe-foundation`. Its
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

GitHub-hosted CI is intentionally deferred to optional ticket `rad-r5dp` to
avoid imposing Actions usage on the sponsoring organization. `make check` is
the required pull-request gate in the meantime.

Secrets are configured through environment files copied from committed
`.env.example` templates. Never commit populated `.env` files.

## Foundation demo

Run the credential-free contract demo first:

```sh
make demo
```

It writes `artifacts/foundation-demo/report.json` and a byte-exact fake TTS
artifact. The report is deliberately labeled as a simulation; it does not
pretend that fake PCM was heard on a phone.

For the physical M1 gate:

1. Deploy and diagnose the pinned media edge using `infra/livekit/README.md`.
2. Start the control plane with production `RADCLIFFE_LIVEKIT_*`, bootstrap,
   internal-token, database, and source-root settings.
3. Start the Pi sidecar with the same internal token, Tool Gateway URL, model
   selection, and provider credential.
4. The default private profile uses an installed Ollama model, MLX Whisper
   `large-v3-turbo`, and the macOS `Samantha` voice converted to 24 kHz PCM.
   Configure the LiveKit session values and run
   `uv run python -m radcliffe_media.production`. The first transcription
   downloads the MLX Whisper checkpoint; subsequent speech stays local.
5. Install the debug app on the Pixel, enter the HTTPS control URL and bootstrap
   token, grant microphone access, then tap Start.
6. Perform the six scenarios in the spec and retain Pixel audio plus the ICE
   candidate report. The LiveKit and Android tickets remain open until direct
   UDP, TURN-only, lock-screen, and speaker runs are attached as evidence.

The local simulation is the fast reviewer entry point. The external deployment
and Pixel run are the acceptance authority for WebRTC, acoustic latency, and
foreground-service behavior.

### Local privacy profile

The LAN launcher defaults are:

- `RADCLIFFE_MODEL_PROVIDER=ollama`
- `RADCLIFFE_MODEL_ID=qwen3:8b`
- `RADCLIFFE_SPEECH_PROVIDER=local`
- `RADCLIFFE_WHISPER_MODEL=mlx-community/whisper-large-v3-turbo`
- `RADCLIFFE_TTS_VOICE=Samantha`

Install and cache the local runtime before starting the launchers:

```sh
brew install ollama ffmpeg
ollama pull qwen3:8b
uv sync --all-packages --all-groups
```

Audio is endpointed in the media worker, transcribed by MLX Whisper, and spoken
with the offline macOS voice. Conversation context and tool calls go only to
the loopback Ollama API. Ollama and Whisper each require a one-time model
download; after both models are cached, turns require no model-provider network
access.
