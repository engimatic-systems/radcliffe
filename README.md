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
   token once, and save the profile. The app generates and persists its own
   installation credential, enrolls idempotently, then deletes the saved
   bootstrap ciphertext. Later launches authenticate as that revocable device
   and load recent conversations. Tap New conversation for a server-generated
   session, or choose an existing conversation to restore its timeline before
   joining; use Settings only when the endpoint or enrollment changes.
6. Perform the six scenarios in the spec and retain Pixel audio plus the ICE
   candidate report. The LiveKit and Android tickets remain open until direct
   UDP, TURN-only, lock-screen, and speaker runs are attached as evidence.

When the user refers to a source without its exact filename, Pi first calls the
separately granted `list_sources` tool. It receives only a bounded catalog of
relative Markdown paths, then passes one returned path to `read_source` for the
canonical, provenance-preserving render path. Neither tool grants general
filesystem access.

An explicit per-source delivery request can add `rate` to `read_source` as
`slow`, `normal`, `fast`, `very_fast`, or a numeric `0.75`–`1.5` multiplier.
The bounded instruction is render metadata, not spoken or model-visible text;
canonical source bytes and hashes remain unchanged across playback and resume.

Conversational behavior changes use the typed `configure` tool. The initial
registry contains only response detail (`brief`, `standard`, `detailed`) and
default speech pace (`0.75x`, `1.0x`, `1.25x`, `1.5x`). Turn overrides expire
with the exact turn; session changes persist for that conversation. Android
shows the effective value and provides bounded set, reset, and undo controls.

Spoken conversation lifecycle requests use the typed `manage_session` tool.
Creating a conversation is immediate and receives a server-generated ID;
switch, restart, and end require enrolled-user confirmation in Android. Named
switches resolve only against the user's catalog and return bounded choices
instead of guessing. Handoffs preserve existing history without copying it,
cancel stale playback and model work, and durably replay exact retries.

Long research or analysis starts through the typed `run_job` tool, which
returns a durable job ID without blocking the foreground conversation.
`manage_job` can inspect, follow up, reprioritize, or cancel by exact ID.
Private job instructions and worker transcripts remain in the job context;
conversation-facing events contain bounded lifecycle metadata and promoted
entity or artifact references only.

Background execution remains opt-in. Operator/user acceptance uses the repository's
Colima/Docker sandbox plus a locally selected Anthropic broker on the trusted host; the control
plane gives that broker only the explicitly approved Anthropic credential and a
private per-attempt workspace,
streams job context on stdin, and accepts only bounded JSON-line lifecycle
frames. Model-selected commands remain inside a credential-free container. Deployment details,
containment, and limits are documented in
[`services/control-plane/README.md`](services/control-plane/README.md#opt-in-executor-process).

The local simulation is the fast reviewer entry point. The external deployment
and Pixel run are the acceptance authority for WebRTC, acoustic latency, and
foreground-service behavior.

### Local privacy profile

This profile is retained as a deprecated, explicit development path. Qwen output
is not accepted as operator/user-test evidence; deterministic tests should use
fakes, and all physical acceptance uses the Haiku profile below.

The LAN launcher defaults are:

- `RADCLIFFE_MODEL_PROVIDER=ollama`
- `RADCLIFFE_MODEL_ID=qwen3:8b`
- `RADCLIFFE_MODEL_CONTEXT_LIMIT=40960`
- `RADCLIFFE_MODEL_RESERVED_OUTPUT_TOKENS=8192`
- `RADCLIFFE_SPEECH_PROVIDER=local`
- `RADCLIFFE_PARAKEET_MODEL=mlx-community/parakeet-tdt-0.6b-v3`
- `RADCLIFFE_PARAKEET_CHUNK_SECONDS=120`
- `RADCLIFFE_PARAKEET_OVERLAP_SECONDS=15`
- `RADCLIFFE_TTS_VOICE=Samantha`
- `RADCLIFFE_MODEL_CHUNK_CHARS=240`

Install and cache the local runtime before starting the launchers:

```sh
brew install ollama ffmpeg
ollama pull qwen3:8b
uv sync --all-packages --all-groups
```

Audio is endpointed in the media worker, transcribed locally by NVIDIA Parakeet
TDT 0.6B v3 through MLX, and spoken
with the offline macOS voice. Conversation context and tool calls go only to
the loopback Ollama API. Ollama and Parakeet each require a one-time model
download; after both models are cached, turns require no model-provider network
access. Set `RADCLIFFE_SPEECH_PROVIDER=whisper` to use the deprecated
`RADCLIFFE_WHISPER_MODEL` rollback path explicitly.

`scripts/run-local-sidecar.sh` forces that local boundary even when cloud
settings exist in `var/.env`; local overrides use
`RADCLIFFE_LOCAL_MODEL_ID`, `RADCLIFFE_LOCAL_MODEL_CONTEXT_LIMIT`, and
`RADCLIFFE_LOCAL_MODEL_RESERVED_OUTPUT_TOKENS`. It never silently falls back to
a cloud model.

### Cloud-backed live-debug profile

The separate `scripts/run-live-sidecar.sh` profile pins Anthropic
`claude-haiku-4-5-20251001`. It starts only when the owner-only `var/.env`
contains both `RADCLIFFE_ALLOW_CLOUD_MODELS=1` and the protected
`RADCLIFFE_ANTHROPIC_API_KEY`. The launcher bridges that value into the one
provider variable, removes the Radcliffe key name before starting Node, and
prints a disclosure that conversation context will leave the Mac. There is no
automatic Ollama fallback.

`RADCLIFFE_CLOUD_MAX_OUTPUT_TOKENS` defaults to 1,024 per foreground turn and
`RADCLIFFE_CLOUD_SESSION_BUDGET_USD` defaults to $0.25. Before every sampling
call, the sidecar uses Anthropic's free token-count endpoint, adds a labeled 2%
plus 32-token safety margin, and reserves the maximum affordable input/output
cost. A mode-`0600` pseudonymous ledger survives restarts; an interrupted
reservation is charged conservatively rather than reopening the ceiling.
Provider, pinned model, accounting basis, and aggregate budget usage appear in
`/healthz`, never prompts, responses, credentials, or raw session IDs.

After a user separately authorizes one paid acceptance request, the doubly
gated `make smoke-haiku-paid` records only provider, model, token usage, and
estimated cost under `artifacts/cloud-haiku-smoke/`. It will not run without
both the normal cloud opt-in and the one-run approval phrase documented in the
script. Ordinary tests use fakes and make no Anthropic calls.

For an operator/user acceptance run, activate and verify the persistent managed
profile with:

```sh
make services-install-user-test
make check-background-jobs-local
```

The first command pins the foreground LaunchAgent to Haiku. The second rejects
the handoff unless the foreground sidecar is pinned to Haiku and the background broker's
locally configured Anthropic model, backend, and sandbox image agree with its command; it never
makes a paid model request.
The Qwen broker remains available only through an explicit deprecated
`--provider ollama` development selector.

The endpoint waits for 900 ms of silence after at least 250 ms of voiced audio
before finalizing an utterance. Tune those local VAD heuristics with
`RADCLIFFE_ENDPOINT_SILENCE_MS`, `RADCLIFFE_ENDPOINT_MIN_VOICE_MS`, and
`RADCLIFFE_ENDPOINT_RMS`. `RADCLIFFE_ENDPOINT_MAX_UTTERANCE_MS` defaults to 120
seconds so a long continuous instruction is not split into a cancelled first
chunk and a retained tail; the limit remains a hard memory and processing bound.
A subminimum burst resets after normal endpoint silence rather
than holding one endpoint open. Maximum-duration candidates must also meet
`RADCLIFFE_ENDPOINT_MAX_MIN_VOICE_RATIO` (default `0.05`). Before Whisper, a
local Silero pass requires at least two speech-like windows. After decoding,
generic segment evidence rejects non-finite, repetitive, no-speech, or very
low-log-probability output. Rejection logs contain only aggregate confidence
and timing—not transcript text or microphone audio—and no phrase is
blacklisted.
During active playback, a sustained near-field candidate that is much stronger
than the normal barge-in floor can recover from Silero's zero-window branch only
after 400 ms and a non-zero confidence floor. The bounded fallback defaults are
`RADCLIFFE_ENDPOINT_STRONG_BARGE_IN_RMS=8000`,
`RADCLIFFE_ENDPOINT_STRONG_BARGE_IN_CONFIRMATION_MS=400`, and
`RADCLIFFE_ENDPOINT_STRONG_BARGE_IN_MIN_PROBABILITY=0.15`; ordinary echo, room
tone, and loud non-speech remain subject to the normal Silero gate.
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

Run the complete Oxford-derived local gate—including an isolated real
control-plane restart, recent-session ordering/resume, exact context
accounting, safe source-tool recovery, false-turn rejection, and the complete
voice loop—with:

```sh
make check-conformance-local
```

The focused session layer is also available as `make check-session-local`. It
uses a generated source root and SQLite database under
`artifacts/session-conformance/`, records a redacted HTTP timeline, and never
restarts or mutates the live phone control plane.

The independent device-auth gate exercises enrollment, replay denial, signed
room-token scope and expiry, refresh, restart, credential rotation, revocation,
and content-free audit. It uses only synthetic secrets in an isolated database
and byte-scans every retained artifact for leaks:

```sh
make check-device-auth-local
```

Operational recovery and secret-rotation procedures are documented in
[`docs/security-operations.md`](docs/security-operations.md).

### Local service operations

The macOS local profile has repository-owned LaunchAgent definitions and a
content-free readiness report. Installation stores no environment values in a
plist; each supervised runner loads the protected `var/.env` at process start.

```sh
make services-install-local
make services-start-local
make services-status-local
make services-restart-local
make services-stop-local
make check-operations-local
make check-recovery-local
make export-diagnostics-local
make audit-logs-local
```

The start, restart, and stop targets use the installed macOS LaunchAgents rather
than spawning ad hoc processes. Each service has one launchd label, repeated
starts are idempotent, and restart waits for the prior process to exit before
starting its replacement. Each lifecycle target prints the resulting service
state and PID. These commands do not change the bootstrap token, device
enrollment, or Tailscale Serve configuration.

`/healthz` is shallow process liveness. `/readyz` additionally checks the
database schema/integrity, warmed Pi sidecar, LiveKit signaling, source root,
and managed-worker supervisor. The local operator workflow and stable failure
codes are documented in [`docs/local-operations.md`](docs/local-operations.md).
SQLite backup, isolated restore verification, upgrade receipts, and confirmed
revision rollback are documented in
[`docs/database-recovery.md`](docs/database-recovery.md).
The correlated content-free event schema, bounded metrics, and private
diagnostic export are documented in
[`docs/telemetry-operations.md`](docs/telemetry-operations.md).
Log lifecycle and bounded failure procedures are documented in
[`docs/operator-recovery.md`](docs/operator-recovery.md).

The focused false-turn corpus is faster and covers digital silence, seeded room
tone, post-AEC playback echo, nearby speech-like babble, foreground speech over
continuing babble, a short noise burst, a 30-second sparse-spike endpoint, and
short legitimate commands across multiple voices:

```sh
make check-stt-rejection-local
```

Publish a static, mobile-friendly review page with the latest test inputs, the
full-duplex LiveKit run, per-check evidence, and indexed run history:

```sh
make build-voice-dashboard
```

By default it writes `~/agent-share/radcliffe/voice-tests/index.html`. The
full-duplex player puts the synthetic microphone on the left channel and the
assistant on the right.

### Opt-in private Pixel STT corpus

Synthetic speech is not acceptance authority for a specific speaker. A local
operator can explicitly enable a bounded, owner-only capture run using
[`docs/stt-capture-plan.example.json`](docs/stt-capture-plan.example.json).
Copy that file under ignored `artifacts/`, replace the owner identity with the
server-issued identity of the enrolled Pixel, tailor the expected phrases, and
protect it with mode `0600`. Then set all three values for the managed worker:

```sh
RADCLIFFE_STT_CAPTURE_ENABLED=1
RADCLIFFE_STT_CAPTURE_PLAN=/absolute/path/to/private-plan.json
RADCLIFFE_STT_CAPTURE_DIRECTORY=/absolute/path/to/private-corpus
```

Capture is off unless the enable flag is exactly `1`; an incomplete setup
fails closed. Only audio from the participant whose LiveKit identity exactly
matches the plan is retained. Plans are capped at 24 utterances and each WAV is
capped at 30 seconds. The capture directory is mode `0700`; WAV and JSON files
are mode `0600`. Private audio and expected/actual text never enter ordinary
worker logs, telemetry, diagnostic exports, or the repository. Each JSON record
retains transport-to-first-frame, endpoint pre-roll/trailing-silence, decoder
confidence, expected/actual text, and the hash of the exact PCM.

Speak the plan in order, disable the flag and restart the worker immediately
afterward, then compare all local candidates against the identical captured
PCM:

```sh
make benchmark-private-stt-local CAPTURE_DIRECTORY=/absolute/path/to/private-corpus
```

The current production profile is selected by default. To gate a measured
winner instead, add `SELECTED_STT_PROFILE=large-v3` (or another evaluated
profile) and then set the matching worker model only after the report passes.

The private report requires macro WER at most `0.03`, exact required-keyword
recall `1.0`, zero dropped leading words, both speaker and earbud routes, and
all required scenario coverage. Its per-case timing and cross-decoder results
classify transport loss, likely endpoint truncation, and decoder error. Delete
the private directory when the evidence is no longer needed.

After measurement selects a genuinely independent verifier, set
`RADCLIFFE_WHISPER_VERIFICATION_MODEL` to its local model ID. Materially
disagreeing local hypotheses and low-confidence primary decodes produce a fixed
request to repeat; uncertain text is never persisted as a user turn or sent to
the model for rewriting.

The current core covers command-level STT WER and vocabulary recall, multiple
English voices, natural and long clause pauses, deterministic
noise/echo/frame loss, paused microphone suppression, consecutive responses
within five seconds, an isolated-store canonical source/tool read, model-message
publication, interrupt acknowledgement, and audible interruption. It does not
store private phone microphone audio. Use `make benchmark-stt-local` to compare
the current turbo model and full large-v3 on the identical generated corpus.

The suite carries forward Oxford's useful behavioral contracts without copying
its legacy transport. Unrestricted file mutation and agent-controlled settings
are intentionally excluded from the current read-only capability policy;
frontier-model probes remain explicitly budget-gated. Phone testing is reserved
for Android lifecycle, Bluetooth controls, lock-screen behavior, network
transitions, and UI rendering.

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
last heard audio after interruption. It also acknowledges playback only after
the subscribed LiveKit track yields audible decoded PCM, then proves those exact
attempt IDs were persisted by the worker. Its isolated database, worker log,
generated inputs, returned-audio timeline, and report are retained together.
The source scenario pauses mid-sentence, verifies audible silence, resumes
without another model/tool call, and audits device-authored flushed and
completed sentence boundaries in SQLite. Successful canonical source rendering
is terminal for its assistant turn, so a provider postamble cannot add a model
message after the source; model text is also filtered through the deterministic
no-emoji output policy before TTS or persistence.

Ordinary model output also passes through the bounded
[spoken-output policy](docs/spoken-output-policy.md). Machine-oriented detail
remains in the durable model envelope and Android timeline, while TTS receives
a truthful category-level alternative. Explicit exact/verbatim requests and
canonical source envelopes bypass this substitution. The content-free Qwen and
Haiku evaluation entry point is `scripts/spoken-output-conformance.py`.
