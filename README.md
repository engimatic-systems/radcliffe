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
   token once, and save the profile. Later launches load the saved profile and
   recent conversations. Tap New conversation for a server-generated session,
   or choose an existing conversation to restore its timeline before joining;
   use Settings only when the endpoint or credential changes.
6. Perform the six scenarios in the spec and retain Pixel audio plus the ICE
   candidate report. The LiveKit and Android tickets remain open until direct
   UDP, TURN-only, lock-screen, and speaker runs are attached as evidence.

When the user refers to a source without its exact filename, Pi first calls the
separately granted `list_sources` tool. It receives only a bounded catalog of
relative Markdown paths, then passes one returned path to `read_source` for the
canonical, provenance-preserving render path. Neither tool grants general
filesystem access.

The local simulation is the fast reviewer entry point. The external deployment
and Pixel run are the acceptance authority for WebRTC, acoustic latency, and
foreground-service behavior.

### Local privacy profile

The LAN launcher defaults are:

- `RADCLIFFE_MODEL_PROVIDER=ollama`
- `RADCLIFFE_MODEL_ID=qwen3:8b`
- `RADCLIFFE_MODEL_CONTEXT_LIMIT=40960`
- `RADCLIFFE_MODEL_RESERVED_OUTPUT_TOKENS=8192`
- `RADCLIFFE_SPEECH_PROVIDER=local`
- `RADCLIFFE_WHISPER_MODEL=mlx-community/whisper-large-v3-turbo`
- `RADCLIFFE_WHISPER_INITIAL_PROMPT=Radcliffe vocabulary: ...`
- `RADCLIFFE_TTS_VOICE=Samantha`
- `RADCLIFFE_MODEL_CHUNK_CHARS=240`

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

The endpoint waits for 900 ms of silence after at least 250 ms of voiced audio
before finalizing an utterance. Tune those local VAD heuristics with
`RADCLIFFE_ENDPOINT_SILENCE_MS`, `RADCLIFFE_ENDPOINT_MIN_VOICE_MS`, and
`RADCLIFFE_ENDPOINT_RMS`. `RADCLIFFE_ENDPOINT_MAX_UTTERANCE_MS` defaults to 30
seconds, and a subminimum burst now resets after normal endpoint silence rather
than holding one endpoint open. Maximum-duration candidates must also meet
`RADCLIFFE_ENDPOINT_MAX_MIN_VOICE_RATIO` (default `0.05`). Before Whisper, a
local Silero pass requires at least two speech-like windows. After decoding,
generic segment evidence rejects non-finite, repetitive, no-speech, or very
low-log-probability output. Rejection logs contain only aggregate confidence
and timing—not transcript text or microphone audio—and no phrase is
blacklisted.
Model speech flushes on sentence and completed-line
boundaries, with `RADCLIFFE_MODEL_CHUNK_CHARS` providing an upper bound for
punctuation-poor output. Phase logs report endpointing, transcription, first
model delta, synthesis, publish, and first-audio timings without message
content. If a later endpoint does arrive while a turn is active, the newer
transcript deterministically supersedes and awaits the old turn before model
work begins.

### Local voice conformance

The Oxford-derived local acceptance suite keeps routine voice verification off
the phone. It generates seeded microphone audio, runs the selected local STT,
drives the real LiveKit room, captures what the client heard, cross-checks
Protocol v2 envelopes and state, and writes WAV plus JSON evidence under
`artifacts/`:

```sh
make check-voice-local
```

The focused false-turn corpus is faster and covers digital silence, seeded room
tone, post-AEC playback echo, a short noise burst, a 30-second sparse-spike
endpoint, and short legitimate commands across six voices:

```sh
make check-stt-rejection-local
```

The current core covers command-level STT WER and vocabulary recall, multiple
English voices, natural and long clause pauses, deterministic
noise/echo/frame loss, paused microphone suppression, consecutive responses
within five seconds, an isolated-store canonical source/tool read, model-message
publication, interrupt acknowledgement, and audible interruption. It does not
store private phone microphone audio. Use `make benchmark-stt-local` to compare
the current turbo model and full large-v3 on the identical generated corpus.

This is the first increment of rad-l1jc/rad-enbx. The remaining Oxford scenarios
are added as their product contracts land: device-authored playback
acknowledgements, randomized source workspaces, file mutation, broader
restart/catalog ordering, and agent-controlled safe settings. Phone testing is
reserved for Android lifecycle, Bluetooth controls, lock-screen behavior,
network transitions, and UI rendering.

Conformance rooms are archived after each run. They remain available through
the authenticated catalog's `include_archived` diagnostic option but do not
clutter the Android recent-conversation home.

With LiveKit, the control plane, and the Pi sidecar running, exercise the same
LiveKit audio and Protocol v2 paths without a phone. The harness creates a
fresh session and starts an isolated worker automatically:

```sh
set -a
source var/.env
set +a
RADCLIFFE_CONTROL_URL=http://127.0.0.1:8000 \
  uv run python scripts/livekit-voice-smoke.py
```

The client synthesizes microphone input, proves paused speech does not become a
turn, requires consecutive responses within five seconds, verifies binary
state/message events, validates an exact canonical source envelope across an
isolated worker database, and measures both interrupt acknowledgement and the
last heard audio after interruption. Its isolated database, worker log,
generated inputs, returned-audio timeline, and report are retained together.
The source scenario also pauses mid-sentence, verifies audible silence, resumes
without another model/tool call, and audits flushed and completed sentence
boundaries in SQLite.
