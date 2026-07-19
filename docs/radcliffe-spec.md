# Radcliffe — Product and Technical Specification

**Status:** Draft 0.1

**Updated:** 2026-07-12

**Reference implementation:** Oxford proof of concept and its independent `oxford-tests` harness

## 1. Product intent

Radcliffe is a local-first, mobile voice interface for working with capable
tool-using models. It should feel like a good conversational partner: the user
can talk, interrupt, pause, resume, and move between conversations naturally.

Its defining capability is **provenance-preserving speech**. A model may decide
that a source should be read, but it must never be responsible for reproducing
that source word-for-word. Canonical source text moves through deterministic
software directly to text-to-speech (TTS), with its provenance visible to the
user and its playback position recoverable after interruption or reconnect.

Radcliffe is initially a single-user product. “Production-ready” means it is
safe and dependable for its owner on a real Android phone across normal mobile
network changes, Bluetooth devices, and lock-screen use; it does not imply that
the first release is a multi-tenant SaaS product.

## 2. Goals and boundaries

### First-release goals

- Natural duplex voice interaction: listen, think, speak, read, interrupt,
  pause, resume, and reconnect.
- A configurable frontier-model backend with structured tool calls.
- Deterministic read-aloud and exact quotation of files and permitted web text.
- Capability-scoped filesystem and web tools, with an auditable action trail.
- Continuity across a transient mobile network failure without duplicated or
  skipped speech.
- A real native Android client that works with the device locked and through
  Bluetooth audio routes.
- An automated acceptance suite that judges the audio actually heard by a
  client, rather than trusting self-reported server state.

### Explicit non-goals for the first release

- A general autonomous computer-use agent with unrestricted machine access.
- A globally distributed, multi-tenant deployment.
- Always-listening or background microphone capture without a user-initiated
  foreground session.
- Treating a model’s generated long-form text as a trusted verbatim source.

### Milestone 1: verifiable foundation demo

The first implementation milestone is a vertical slice, delivered as one
reviewable PR from `epic/radcliffe-foundation`. It proves the architecture on
the target Pixel before broader production hardening.

The milestone is accepted by performing this demo:

1. Launch the native Android app, tap Start, and connect through the self-hosted
   LiveKit deployment to the Radcliffe worker.
2. Ask an ordinary question by voice. The app shows the user transcript and
   speaks a frontier-model answer, with first meaningful audio within five
   seconds under the demo conditions.
3. Interrupt the answer by speaking or tapping. Audible playback stops within
   500 ms and the new turn is accepted.
4. Ask Radcliffe to read a provided Markdown fixture. Pi issues the typed
   `read_source` call through the capability-scoped Tool Gateway; the canonical
   source bypasses model generation, appears with source provenance in the UI,
   and is sent exactly to TTS.
5. Pause and resume the source reading using both the app and a system media
   control. Reading resumes from the persisted playback boundary.
6. End and restart the app session without leaving an orphaned worker or tool
   operation. The generated demo report contains latency, interruption,
   canonical-text, state-transition, and tool-audit evidence.

Milestone 1 uses one target Pixel, one configured frontier-model provider, one
STT provider, one TTS provider, one self-hosted LiveKit node, and the read-only
`read_source` tool. Device matrices, hostile-network coverage, mutating tools,
and operational hardening follow as smaller milestones.

## 3. Architectural decisions

| Concern | Decision | Why |
| --- | --- | --- |
| Realtime media | Self-hosted LiveKit Server (OSS) | Mature WebRTC room, ICE, TURN, reconnect, and native-client layer without managed-service lock-in. |
| Agent media adapter | Pipecat `LiveKitTransport`, behind a Radcliffe interface | Keeps STT, LLM, TTS, and transport providers replaceable. |
| Agent orchestration | Version-pinned Pi Agent Core sidecar behind `AgentOrchestrator` | Supplies a typed model/tool loop, custom message projection, streamed tool lifecycle events, and cancellation signals without owning Radcliffe state. |
| Conversation core | Radcliffe-owned state machine and event model | Playback cursors, provenance, tools, and cancellation are product logic, not framework behavior. |
| Android app | Kotlin + Jetpack Compose + LiveKit Android SDK | Native control of microphone, Bluetooth, lock-screen media controls, and foreground-service lifecycle. |
| Control protocol | Versioned Protobuf envelopes over LiveKit reliable data packets; JSON at REST/debug boundaries | Typed, compact, backwards-compatible events across Kotlin and Python. |
| Agent runtime | Python 3.12 media/control plane plus Node.js 22.19+ Pi sidecar | Keeps local speech tooling in Python while using Pi for agent/tool orchestration. |
| Persistence | SQLite/WAL plus migrations for v1 | Simple local-first durability; can later move event storage without changing the protocol. |
| Dependency management | `uv` lockfile for Python; Gradle version catalog for Android | Reproducible builds; no inherited virtual environment. |

### Topology

```text
Android app ── WebRTC audio + data ──> LiveKit media edge <── WebRTC ── Radcliffe media worker
     │                                       │                                  │
     └──── HTTPS token/control API ──────────┴──────────────────────────────────┤
                                             │                                  │
                                      TURN/UDP + TURN/TLS               AgentOrchestrator
                                                                                │
                                                                         Pi Agent Core
                                                                                │
                                                                          Tool Gateway
                                                                                │
                                                                       capability workers
```

The initial robust deployment is a small self-hosted Linux VM with a public
domain, TLS, LiveKit, and its embedded TURN service. The agent worker may run
on the owner’s Mac and makes an outbound connection to the room; it therefore
does not require inbound access to the home machine. The control plane issues
short-lived room tokens and never exposes LiveKit API secrets to the app.

For strict home-only use, a `local` deployment profile may run the media server
over Tailscale. It is not the baseline for mobile reliability away from the
tailnet; the public media-edge profile is.

Pipecat is an adapter, not the owner of Radcliffe state or protocol semantics.
An early integration spike must prove that its LiveKit adapter supports the
required cancellation, cursor, and data-packet behavior. If it does not, only
that worker-side adapter is replaced with a direct LiveKit participant adapter;
the Android client, media edge, envelopes, and conversation engine remain the
same.

Pi Agent Core is a similarly bounded adapter. It receives Radcliffe context
packets, performs the provider and structured-tool loop, and emits lifecycle
events. Radcliffe retains the canonical event store, playback ledger,
projection rules, capability grants, and tool workers. The Pi adapter is pinned
to an exact tested release and validated by Radcliffe conformance tests before
an upgrade.

## 4. WebRTC robustness requirements

LiveKit owns commodity WebRTC concerns that Oxford had to hand-roll: signaling,
ICE candidate gathering, NAT traversal, TURN fallback, media encryption,
connection recovery, device audio routing, and room lifecycle. Radcliffe must
still own product-level recovery above that layer.

- Enable direct ICE/UDP, TURN/UDP, WebRTC-over-TCP, and TURN/TLS. Configure
  TURN/TLS on port 443 where a load balancer does not terminate it.
- Use a trusted public TLS certificate and a dedicated TURN hostname.
- Treat `Room` reconnect as transport recovery, not as a new conversation.
  On reconnect, the client and worker exchange their last acknowledged
  `MessageEnvelope` and playback cursor and resume idempotently.
- Send commands, state changes, and playback acknowledgements over reliable
  data packets. Audio remains on WebRTC media tracks.
- Treat audio-track failures, data-channel failures, and agent-worker failures
  independently. Each has a bounded retry policy and a clear user-visible
  state.
- Test UDP-blocked networks, TURN-only operation, Wi-Fi-to-cellular changes,
  captive portals, background/foreground transitions, process restarts, and
  packet loss/reordering.

The initial LiveKit server is single-node, deliberately. A later high-
availability deployment may add Redis, multiple media nodes, and a load
balancer only after the single-user product warrants that complexity.

## 5. Android application stack

The Android client is the primary production client. A web app may remain a
developer and desktop client, but it is not the mobile reliability baseline.

- **Language and UI:** Kotlin, Gradle Kotlin DSL, Jetpack Compose, Material 3,
  Navigation Compose.
- **State:** coroutines and `StateFlow`, ViewModels, Hilt dependency injection,
  DataStore for settings, and Room for a small offline cache of envelopes and
  reconnect state.
- **Local connection profile:** DataStore persists the control URL and current
  compatibility session ID. The bootstrap credential is encrypted with
  AES-256-GCM under a non-exportable Android Keystore key; DataStore contains
  only a versioned `v1` ciphertext envelope. App backups and screenshots are
  disabled, and clearing settings removes both preferences and key material.
  Device enrollment under `rad-nmdx` exchanges the decrypted legacy bootstrap
  credential once for a client-generated per-installation credential. The
  control plane stores only its digest, binds room identity server-side, rejects
  bootstrap authorization on ordinary session routes, and supports immediate
  operator revocation. Android persists the enrolled credential through the
  same Keystore-backed repository boundary and then deletes the bootstrap
  ciphertext; the non-exportable key remains because it protects the enrolled
  credential.
- **Realtime:** the official LiveKit Android SDK, configured for two-way
  communication audio. The app publishes microphone audio and subscribes to
  the agent’s audio track; control events use LiveKit data packets.
- **Session ownership:** a `VoiceSessionService` owns the LiveKit room,
  playback state, and reconnect logic. It is a foreground service declared as
  both `microphone` and `mediaPlayback`, started only by an explicit user
  action after microphone permission is granted.
- **Lock screen and hardware controls:** AndroidX Media3 `MediaSessionService`
  exposes pause, resume, stop, and activity state to the notification, lock
  screen, earbuds, Bluetooth devices, and Android Auto where appropriate.
- **Audio:** use Android communication audio mode, audio focus, audio-device
  callbacks, and LiveKit’s audio routing. Support speaker, wired headset,
  Bluetooth, and Bluetooth reconnection explicitly.
- **Local barge-in:** a small on-device VAD/energy detector may immediately
  mute/flush local agent playback and send a barge-in request. Server-side VAD
  remains authoritative for turn finalization. A tap-to-interrupt control must
  always work even if local VAD is unavailable.
- **Testing:** coroutine/unit tests, Compose UI tests, instrumented service and
  audio-route tests, network-chaos tests, and physical-device acceptance runs
  on the target Pixel plus at least one additional Android device.

Android’s foreground-service policy is part of the design: microphone capture
must be user initiated while the app is foregrounded. Radcliffe must not try to
evade this restriction with a permanently hidden audio service.

## 6. Conversation and playback state

The conversation engine is an explicit state machine:

```text
idle → connecting → listening → thinking → speaking | reading
                      ↑            │          │          │
                      └────────────┴──────────┴──────────┘
                                   paused ↔ reconnecting
                                            ↓
                                          failed
```

Every asynchronous operation carries a session ID, monotonically increasing
generation, operation ID, and cancellation token. A newer user turn,
interruption, pause, disconnect, or session switch invalidates stale work.
Cancelled work may complete internally, but it must never produce audible
output or mutate a newer conversation.

### Playback ledger

The client is the source of truth for what was heard. For each audio segment it
acknowledges `started`, periodic position, `completed`, `flushed`, or `failed`.
The server persists this ledger and uses it to:

- resume at the correct sentence or word boundary;
- create accurate interruption context;
- avoid speaking duplicated content after reconnect;
- distinguish generated speech from source reading; and
- provide audio-grounded diagnostics to the acceptance suite.

Before publishing a segment, the worker announces its immutable operation,
segment, boundary, duration, content digest, source kind, and unique playback
attempt ID over the reliable data channel. The Android service attaches a sink
to the decoded LiveKit audio track and emits acknowledgements from PCM consumed
by that sink—not from server enqueue time or a timer. Pause, interrupt, and stop
flush unfinished attempts before their corresponding control; track loss fails
them while the data channel remains available. The worker rejects unknown attempts,
metadata mismatches, invalid transitions, backwards positions, and premature
completion; it advances a source boundary only after a valid device-authored
`completed` acknowledgement. A resumed segment gets a fresh playback attempt
ID, so delayed acknowledgements from an earlier attempt cannot advance it.

The local LiveKit acceptance client implements the same decoded-audio contract
and cross-checks every persisted source attempt against the acknowledgements it
sent. Unit tests retain an explicit server-timed compatibility mode only for
isolated pipeline fixtures; production workers always require client authority.

## 7. Message envelopes and the projection seam

This is a mandatory architectural seam, even though the first release will
ship only a small set of projection rules.

Every conversational item is represented by a versioned, immutable
`MessageEnvelope` before it is rendered, persisted, or sent to a model. It is
not permissible for the system to collapse history into untyped transcript
strings.

```protobuf
message MessageEnvelope {
  uint32 schema_version = 1;
  string message_id = 2;
  string session_id = 3;
  uint64 sequence = 4;
  SourceKind source = 5;          // USER, MODEL, CALL, TEXT_SOURCE, SYSTEM
  ContentRef content = 6;         // canonical inline text or immutable reference
  bytes content_sha256 = 7;
  google.protobuf.Timestamp created_at = 8;
  map<string, string> metadata = 9;
  Provenance provenance = 10;
  Sensitivity sensitivity = 11;
}
```

`CALL` covers structured tool/function invocations and results; its metadata
must include the tool name, argument/result references, capability grant, and
outcome. `TEXT_SOURCE` covers document chunks, web extracts, quotations, and
other canonical source text; its provenance must include the path or URL,
extractor version, content hash, and permissions basis.

### Deterministic projections

Each ordered envelope is passed through independently versioned projection
pipelines:

```text
MessageEnvelope
    ├── RenderProjection  → RenderPlan  → TTS / visual presentation
    └── ContextProjection → ContextPacket → model input
```

- **RenderProjection** decides whether, how, and in what segments the canonical
  message can be spoken. It must support provenance labels, interruption,
  pause/resume, cancellation, and a playback cursor.
- A `read_source` call may include `rate` only for an explicit user delivery
  request. Named rates map to the closed provider-independent range below;
  numeric multipliers are rounded to three decimal places and rejected outside
  `0.75` through `1.5` rather than silently clamped.

  | Delivery | Multiplier | Local Apple TTS |
  | --- | ---: | ---: |
  | slow | `0.75x` | 135 words/minute |
  | normal | `1.0x` | 180 words/minute |
  | fast | `1.25x` | 225 words/minute |
  | very fast | `1.5x` | 270 words/minute |

  OpenAI TTS receives the same bounded multiplier in its `speed` field. A
  legacy provider without rate support continues at normal speed, but a
  non-default instruction fails visibly instead of being ignored.
- Non-default delivery is stored only in reserved per-boundary envelope
  metadata. It never changes message content or its SHA-256, and all
  `radcliffe.delivery.*` keys are removed from ContextProjection. The playback
  announcement carries the multiplier for device diagnostics; Android renders
  a non-default accessible label. Resume and replay re-derive the rate from the
  persisted boundary metadata, so no mutable current-rate state can leak into
  another snippet.
- **ContextProjection** produces a structured model-context item based on the
  envelope’s content, source kind, provenance, permissions, sensitivity, and
  metadata. It may select, redact, annotate, or reference content according to
  explicit policy.
- Both projections must be deterministic, replayable, idempotent, testable,
  auditable, and versioned. Replaying the same envelope stream with the same
  projection versions must produce the same render and context plans.
- Projection rules are pure policy/data transformations; they do not execute
  arbitrary tool code. If a model creates a summary or interpretation, that is
  recorded as a new `MODEL` envelope rather than hidden inside a projection.
- Provider output is canonicalized before it can become a `MODEL` envelope or
  reach TTS. The initial policy removes emoji deterministically even when a
  provider ignores the system instruction; later content rules belong at this
  same provider-independent boundary.
- A successful canonical `read_source` handoff is terminal for that assistant
  turn. The `TEXT_SOURCE` envelope is rendered directly, while later provider
  model deltas or postambles are discarded rather than entering TTS, UI, or
  future context. The tool request, result, and source provenance remain
  auditable.
- The seam must be independent of the WebRTC transport, TTS provider, model
  provider, and mutable live-session state. New rules must be deployable
  without rewriting stored history or the conversation engine.

This design allows a later feature to read every eligible message
deterministically while injecting source-aware context for user speech, model
output, calls, and text sources.

### Deterministic agent-control action boundary

Behavior changes and session transitions cross one versioned boundary:

```text
spoken request → structured model tool call → CALL audit envelope
               → AgentControlActionRequest → policy registry
               → confirmation when required → authorized executor
               → AgentControlActionResult → UI and audit projection
```

Model prose is never parsed as a command. Pi may expose the small typed
`configure` and `manage_session` tools, while an exceptional `delivery.rate`
argument may accompany a read/snippet request. The Tool Gateway converts those
typed requests into the canonical protobuf envelope; neither the model nor the
tool name is authority to mutate application state.

Every request uses action schema version `1` and declares:

- a stable request ID and registered `AgentControlActionId`;
- the active `OperationRef`, including session, generation, operation,
  cancellation, and trace IDs;
- an `AgentControlOrigin` containing source kind, actor ID, source-message ID,
  and a tool-call ID only for `CALL` provenance;
- an exact snippet, turn, session, device, or operator-default target;
- a session-monotonic request sequence and stable idempotency key;
- the confirmation policy expected by the registry; and
- exactly one typed action payload and a timestamp.

`USER`, `MODEL`, `CALL`, `TEXT_SOURCE`, and `SYSTEM` origins remain distinct in
the audit record. A normal model-driven control request should be `CALL`, linked
to the user message that caused the typed tool call. Raw `MODEL` origin is
available for a provider adapter that supplies equivalent structured output,
but its capability grant may independently forbid it. `TEXT_SOURCE` may affect
only that source snippet's delivery. `SYSTEM` is reserved for an authenticated
operator path and never means that arbitrary system text is executable.

The initial registry is closed:

| Stable action ID | Typed payload | Allowed scope | Required confirmation |
| --- | --- | --- | --- |
| `speech_delivery.set_rate` (`1`) | rate enum (`0.75x`, `1.0x`, `1.25x`, `1.5x`) or one numeric multiplier from `0.75` through `1.5` | snippet | none |
| `configuration.set` (`2`) | `response_detail` or `default_speech_rate` with its matching enum value | turn or session | none |
| `configuration.set` (`2`) | same allowlisted keys | device | authenticated user |
| `configuration.set` (`2`) | same allowlisted keys | operator default | authenticated operator |
| `session.create` (`3`) | empty | session | none |
| `session.switch` (`4`) | destination session ID | session | authenticated user |
| `session.restart` (`5`) | empty | session | authenticated user |
| `configuration.inspect` (`6`) | one allowlisted configuration key | turn, session, device, or operator default | none |
| `configuration.reset` (`7`) | one allowlisted configuration key | turn or session / device / operator default | none / authenticated user / authenticated operator |
| `configuration.undo` (`8`) | one allowlisted key and prior mutation ID | turn or session / device / operator default | none / authenticated user / authenticated operator |
| `session.end` (`9`) | empty | session | authenticated user |

There is no free-form setting name or generic arguments object in the canonical
contract. The strict sidecar JSON type rejects extra properties before protobuf
conversion. The protobuf contract has no filesystem path, web URL, command,
environment, secret, credential, download, privilege, or process-execution
field. Such capabilities require separate tools, grants, schemas, and
confirmation policies; they cannot be smuggled through agent control.

The `manage_session` tool is also a closed union: create, switch with one
bounded reference, restart, or end. Switch references resolve only against the
enrolled principal's non-archived catalog. An exact owned session ID wins;
otherwise exact or partial titles and generic recent-conversation references
produce at most three choices when they are not unique. The executor never
guesses a destination.

Create and restart allocate a server-owned session ID and begin with empty
history. Switch preserves both histories without copying content. A destination
worker must be ready before one transaction records the transition and ends the
source generation; startup failure restores the source as active and stops any
partial destination worker. Exact completed retries replay the durable result,
including after control-plane restart, while changed retries conflict. After
commit, the source loses tool authority, the media worker cancels playback and
model output, persists a content-free transition audit, and closes its turn.
Android binds confirmation to its enrolled participant identity, persists a
non-end destination before loading its timeline or credentials, replaces the
room, and keeps that destination selected as the safe recovery target if the
live reconnect fails. End clears the selected session.

### Registered behavior configuration

The initial configuration registry is deliberately closed:

| Key | Schema and bounds | Built-in default | Allowed scopes | Persistence |
| --- | --- | --- | --- | --- |
| `response_detail` | enum: `brief`, `standard`, `detailed` | `standard` | turn, session, device, operator default | turn expires with its exact turn ID; all other layers are durable |
| `default_speech_rate` | enum: `0.75x`, `1.0x`, `1.25x`, `1.5x` | `1.0x` | turn, session, device, operator default | turn expires with its exact turn ID; all other layers are durable |

Effective precedence is built-in, operator default, device, session, then exact
turn. A tombstoned reset reveals the next lower layer. Each target has a
monotonic revision; authenticated clients may provide an expected revision so
concurrent devices receive a visible conflict instead of silently overwriting
one another. Mutation and idempotency IDs make exact retries replay their first
result, while changed retries fail. Undo succeeds only when no later mutation
has occupied the target.

The model-visible `configure` tool supports inspect, set, reset, and undo for
only these two keys and only turn or session scope. Common spoken synonyms are
normalized before registry application, but stored values remain canonical.
Device changes are explicit authenticated Android actions and operator-default
changes remain outside model authority. Credentials, endpoints, grants,
developer options, arbitrary preference keys, and paid-provider policy have no
registry entry and therefore cannot be inspected or changed through this path.

The worker resolves response detail before sending a turn to the sidecar and
resolves default speech pace at each playback boundary. An explicit snippet
delivery instruction still wins, including an explicit `1.0x`. Configuration
audit envelopes are durable and available to Android, but excluded from later
ContextProjection. Android loads the authoritative effective registry on
reconnect, applies live updates, and exposes bounded choices plus reset and
undo.

The policy boundary validates in this order: supported schema and safe IDs;
registered action and matching payload; target shape and bounded typed argument;
origin/action/scope capability grant and expiry; current operation generation;
idempotency and precedence-slot collision; then declared confirmation policy.
It emits only stable rejection codes:

- unsupported schema, malformed envelope, or unregistered action;
- unauthorized origin/action/scope;
- invalid scope, target, or typed argument;
- stale generation;
- idempotency conflict or concurrent precedence conflict;
- confirmation-policy mismatch; or
- cancelled/already-authorized state.

An exact retry with the same idempotency key and deterministic request bytes
returns the first durable decision without reapplying the action. Reusing that
key with different bytes is rejected. Different requests with the same action,
configuration key, target, scope, generation, and request-sequence slot are
also rejected instead of using arrival order. A retry after reconnect therefore
cannot silently replace a previous request, and work from an older generation
cannot mutate the current session.

When multiple already-authorized settings are applicable, precedence is:

```text
snippet > turn > session > device > operator default
```

Within one scope, a newer generation wins, then a greater session-monotonic
request sequence. Two different payloads tied at that slot are a conflict, not
a lexical or timing-based winner. Precedence is evaluated only among requests
for the same stable action/configuration key and applicable target; it never
allows one action family to override another.

Confirmation is itself a versioned, idempotent message tied to the same
operation. User confirmation requires `USER` provenance; operator confirmation
requires authenticated `SYSTEM` provenance. Denial becomes cancellation. A
cancellation may arrive before the request and leave a tombstone, or cancel a
request awaiting confirmation. Once the boundary has released an action as
`AUTHORIZED`, cancellation cannot pretend to roll back an executor that may
already have applied it; reversal is a new typed action with its own audit
record. Session-switch executors must durably preserve history and cancel or
rehome owned work before reporting `APPLIED`.

The result distinguishes `AWAITING_CONFIRMATION`, `AUTHORIZED`, `APPLIED`,
`REJECTED`, `DUPLICATE`, `CANCELLED`, `FAILED`, and `SUPERSEDED`. The policy
boundary reports authorization; the state owner alone may report application,
including an applied sequence and resulting state digest. Audit projection
stores schema/request/action IDs, operation and provenance IDs, target scope,
request sequence, idempotency key, declared confirmation, request SHA-256,
disposition, rejection code, and replay status. It excludes credentials,
message text, and arbitrary argument serialization.

Unknown protobuf fields are preserved when forwarding a Protocol v2 message,
but the active boundary rejects an unknown action or enum value. New optional
fields and new registered IDs are additive minor changes. Existing numeric IDs,
scope meanings, confirmation requirements, replay behavior, and precedence
cannot change in place; incompatible semantics require a new field or Protocol
v3.

### Durable background-job lifecycle

Long research, analysis, download, and coding work crosses a separate bounded
job boundary. The foreground model calls `run_job` with a private instruction,
notification policy, priority, runtime limit, and optional USD budget. The
control plane durably accepts the request and returns a `job_id` immediately;
it does not hold the foreground tool turn open while an executor works.

Each job has an independent monotonic revision, status (`queued`, `running`,
`waiting`, `succeeded`, `failed`, or `cancelled`), execution attempt, and
short-lived lease. A transaction grants only one executor a queued job. An
expired running lease may be recovered by a different executor and creates an
explicit `lease_recovered` event. Heartbeats extend only the current owner's
lease. A stale or foreign executor cannot publish progress or completion.

`manage_job` and the enrolled-device API expose only four bounded operations:
inspect status, add a private follow-up, set priority from `0` through `4`, or
request cancellation. Mutations are idempotent; reusing a command key with
different input conflicts. Queued and waiting cancellation is immediate.
Running cancellation records intent first, and a later success cannot overtake
that committed request; the executor must acknowledge cancellation.

Private primary instructions and follow-up text live only in the separate job
record returned to the authenticated internal executor. Conversation-facing
`JobEvent` messages contain lifecycle metadata, stable detail codes, and
promoted entity/artifact IDs. They have no instruction, follow-up, raw command
output, worker transcript, or generic text field. Event revisions replay after
restart, allowing the later foreground notification scheduler to apply
`immediate`, `next_safe_pause`, `silent`, or `user_waiting` policy without
reconstructing private job context.

### Deterministic context-budget accounting

Context usage is measured after `ContextProjection` and Pi's custom-message
conversion, at the exact serialized provider-request boundary. The configured
model contract supplies a context limit and a reserved output budget. The
session metric contains projected input tokens, reserved output tokens,
remaining tokens, percentage of input capacity consumed, projection version,
model ID, measurement basis, whether the count is exact, and overflow state.
The public metric never includes message content or a request digest.

For the local Ollama profile, the sidecar captures the same Pi payload used for
generation and obtains the provider's authoritative prompt-token count with a
one-token dry run over loopback. It caches the result by a SHA-256 digest of the
model-visible request. The real generation is rejected before submission when
input plus the reserved output exceeds the configured limit. A provider error
or silent truncation at the model's maximum window is reported honestly as an
inexact overflow lower bound, not as an exact count. Provider adapters that
cannot supply exact preflight accounting must label estimates and may not
silently claim token precision.

The temporary live-debug cloud profile is a separate explicit launch path. It
accepts only Anthropic `claude-haiku-4-5-20251001`; an alias or different cloud
model is a startup error until a new production profile is reviewed. Anthropic's
token-count endpoint receives the same structured model context and is treated
as an estimate: Radcliffe adds 2% plus 32 tokens, reports `exact=false`, and
includes the provider/model in the accounting basis. No count is reused across
a model or projection change.

Before every cloud sampling call, the sidecar reserves conservative input cost
at the highest enabled cache-write rate plus the maximum affordable output.
The configurable output cap applies across all sampling calls in one foreground
turn, while a durable per-session USD ceiling covers the whole session. The
owner-only ledger contains only session pseudonyms, the provider/model/pricing
basis, token totals, cost, and outstanding reservations. A sidecar restart
charges an abandoned reservation rather than reopening budget. Exhaustion is a
visible terminal event emitted before another paid generation request.

Replaying the same envelopes, model configuration, and projection version must
produce the same provider payload digest and accounting result. Interrupted,
superseded, failed, secret, tool-call, and canonical text-source envelopes
follow their actual model-visible projection policy. Compaction or a model
change necessarily creates a new payload digest and fresh count.

### Turn disposition and supersession policy

Every voice turn tags its user and model envelopes with a stable
`radcliffe.turn_id`. Completion, interruption, failure, or replacement appends
an immutable `SYSTEM` audit envelope with `radcliffe.event=turn_disposition`
and `radcliffe.turn_disposition` set to `completed`, `interrupted`, `failed`,
or `superseded`. Existing envelopes are never rewritten or deleted.

`ContextProjection` version `context-v2` derives the latest disposition for
each turn during replay. It excludes disposition audit envelopes and replaces
the model-visible content of interrupted, failed, or superseded turns with a
deterministic exclusion marker. Completed turns remain eligible under the
normal source, provenance, permission, and sensitivity rules. Thus the local
audit retains abandoned user speech and partial model output without allowing
either to become an accidental instruction in a later turn.

Only one endpointed transcript may own a session at a time. A newer transcript
cancels and awaits the active owner; if multiple transcripts are pending, the
newest submission wins and older pending submissions are recorded as
superseded without invoking the model. This replacement policy is deliberate:
Radcliffe does not concatenate separately endpointed transcripts because doing
so could silently change user intent.

An RMS endpoint is only a speech candidate. A higher foreground threshold
confirms that a candidate can become a turn, and end silence follows that
foreground threshold so quieter continuing room speech cannot indefinitely
hold an utterance open. Unconfirmed background candidates have a short bound.
Automatic barge-in uses a still higher near-field threshold, sustained-speech
debounce, and speech-activity evidence before it can flush audible output.
Subminimum bursts reset after the normal silence window, and sparse
maximum-duration candidates are rejected by voice occupancy before STT.
Remaining local candidates pass a speech-likeness model and then a
decoder-evidence policy using segment no-speech probability, average log
probability, compression ratio, and finite-value checks. Rejected candidates
create no `USER` envelope or model call. The policy is based on acoustic and
decoder evidence, never a blacklist of transcript phrases, and logs only
aggregate evidence without retaining phone microphone audio.

### Pi Agent Core adapter

`AgentOrchestrator` maps Radcliffe envelopes to Pi custom agent messages and
maps Pi events back to Radcliffe envelopes. The adapter uses Pi’s
`transformContext` and `convertToLlm` extension points to apply the
already-versioned Radcliffe `ContextProjection`; projection decisions remain
replayable Radcliffe policy rather than incidental agent state.

The adapter produces the following canonical event flow:

```text
ContextPacket → Pi provider request → MODEL envelope(s)
Pi tool request → CALL envelope → Tool Gateway → CALL result envelope
Tool progress → CALL progress envelope → truthful user-visible progress policy
```

Pi tool definitions use schema-typed arguments and streamed execution updates.
The initial policy uses sequential tool execution. Radcliffe may permit
parallelism only for explicitly classified, independent, read-only tools after
conformance and race-condition tests demonstrate that it is safe.

## 8. Deterministic reading and quotation

1. A model requests a typed `read_aloud` or `quote` tool with a `DocumentRef`.
2. The document service resolves the permitted source, extracts canonical text,
   removes only specified metadata, and records extractor/version/content hash.
3. A `TEXT_SOURCE` envelope and deterministic `RenderPlan` are created.
4. Text segments go directly to TTS. The model neither reconstructs nor
   paraphrases the source.
5. The client renders sourced speech distinctly and records playback progress.
6. Resume uses the persisted cursor, not a new model guess.

The system verifies exactness at two levels: byte-for-byte canonical input to
TTS, plus end-to-end acoustic evaluation against audio produced on real devices.

## 9. Agent tools and security model

- Models use schema-validated structured tools, never text directives parsed
  from model prose.
- Pi Agent Core routes every requested tool through the Radcliffe Tool Gateway.
  The gateway creates the canonical `CALL` envelope, makes an immutable copy of
  arguments, validates them against the Radcliffe tool schema, evaluates the
  current capability grant, records the authorization decision, and dispatches
  the permitted work to a capability worker.
- Tool results and streamed updates return through the gateway, are persisted
  as canonical envelopes, and are then supplied to Pi as typed tool results.
  For deterministic rendering, the gateway also carries the authorized
  `TEXT_SOURCE` envelope through Pi's structured `details` field. Only the
  content-free authorization notice enters the model-visible tool-result
  content. The media worker validates the envelope's session, message ID,
  content hash, source kind, and provenance hash before persisting and
  rendering it; this works whether gateway and worker stores are shared or
  isolated.
  Tool cancellation propagates from the Radcliffe operation token through Pi’s
  abort signal to the capability worker.
- When the user refers to a document informally, Pi may first invoke the
  separately granted `list_sources` tool. Its model-visible result is a bounded,
  sorted catalog of relative Markdown paths only. The agent must select an
  exact returned path for `read_source`, or ask the user when more than one
  candidate is plausible; it cannot select a directory or browse outside the
  grant.
- Each session receives explicit capability grants: readable roots, writable
  roots, allowed web domains, maximum download sizes, and permitted actions.
  The control plane monotonically ratchets the active operation generation on
  authenticated tool calls, refreshes the in-memory grant for a newer
  generation, and rejects later calls from an older interrupted operation.
- Filesystem resolution uses safe path containment, symlink policy, and a
  sandboxed tool worker. “Live” mode must still be capability scoped.
- Destructive writes, downloads, shell execution, or privilege changes require
  confirmation appropriate to the action. Unrestricted shell access is not a
  first-release feature.
- Web extraction treats fetched content as untrusted data, never instructions.
  URL fetching has SSRF, redirect, content-type, size, and archive-safety
  controls.
- Tool requests/results, grants, confirmation decisions, and errors are stored
  as envelopes and audit records.
- Audio retention is opt-in. Transcript, source, and credential handling must
  follow least-privilege and user-configured retention policies.

## 10. Reliability targets

| Measure | Initial target |
| --- | --- |
| Tap-to-interrupt local playback | p95 ≤ 200 ms |
| Automatic barge-in to audible silence | p95 ≤ 500 ms |
| First meaningful speech after end of user turn | p95 ≤ 4 s; p99 ≤ 5 s |
| Pause to silence | p95 ≤ 300 ms |
| Transient reconnect to usable session | p95 ≤ 3 s |
| Source text passed to TTS | 100% canonical-text match |
| Duplicate/skipped speech after reconnect | 0 in conformance corpus |
| Long-running stability | 8-hour soak without unrecoverable session loss |

The first-audio SLO must not be met by misleading filler. If a long tool action
is in progress, the agent may provide a brief truthful progress cue, but it
must not make a chime or placeholder appear to be an answer.

## 11. Test strategy and release gates

Oxford’s independent test-suite concept becomes Radcliffe’s conformance suite.
It must run on a separate machine or isolated capacity so the evaluator cannot
distort latency results through STT/TTS contention.

Test layers:

1. Unit/property tests for state transitions, cancellation, cursor arithmetic,
   path containment, capability decisions, and projection determinism.
2. Provider-contract tests using fake STT, LLM, TTS, VAD, and tool providers.
3. Protocol tests using prerecorded and generated audio with deterministic
   clocks and network simulation.
4. End-to-end audio tests for response content, latency, interruption,
   pause/resume, exact reading, quotation, reconnection, settings, and tools.
5. Android device tests for screen lock, foreground service lifecycle, earbuds,
   Bluetooth switching, Wi-Fi/cellular transition, and app process recovery.
6. Security tests for prompt injection, path traversal, symlink escape, SSRF,
   malicious archives, unauthorized tools, and stale-token access.

The retained synthetic STT rejection corpus includes digital silence, seeded
room tone, post-AEC playback echo, nearby speech-like babble, foreground speech
over continuing babble, sparse short noise, a 30-second maximum-duration spike
pattern, and legitimate short speech across supported test voices. It gates
false `USER` turns independently from ordinary WER and keyword-recall
benchmarks. Each release candidate also publishes a static review dashboard
containing playable test inputs, per-check evidence, and a stereo full-duplex
run with microphone input on the left and assistant output on the right.
7. Soak, load, and chaos tests for agent crashes, provider timeouts, TURN-only
   networks, packet loss, and CPU/GPU contention.

### Cloud-provider test budget

Automated and exploratory tests use fake providers or the local Ollama,
Whisper, and TTS profile by default. The presence of a funded OpenAI or
Anthropic credential in an ignored environment file is not permission to use
it. Cloud model and speech calls remain disabled unless the user explicitly
approves that test run and the operator sets `RADCLIFFE_ALLOW_CLOUD_MODELS=1`
for that process. A cloud-provider acceptance run should be made only after
the deterministic local suite passes, should use a bounded scenario set, and
should record provider, model, token usage, and reported cost so one controlled
run can be evaluated before another is authorized.

The Haiku acceptance smoke adds a second one-run approval phrase and writes an
owner-only, content-free report. The report may contain provider, pinned model,
token classes, estimated USD cost, budget basis, and pass/fail status; it must
not contain the prompt, model response, key, request payload, or raw session ID.

Release requires repeated clean conformance runs, all security tests passing,
SLOs met under isolated and realistic load, and two weeks of dogfood use with
no severity-one loss of data, privacy, or recoverable session continuity.

## 12. Delivery sequence

### Milestone 1 PR

1. Freeze the minimum Protocol v2 envelope/state contracts, capability model,
   and threat model needed by the vertical slice.
2. Create the reproducible monorepo and CI baseline.
3. Implement envelope persistence, deterministic projection, the state
   machine, and playback acknowledgements against fake providers.
4. Prove LiveKit connectivity and the Android foreground session on the target
   Pixel.
5. Integrate the streaming VAD/STT/Pi/TTS pipeline.
6. Route the typed `read_source` call through the read-only Tool Gateway and
   deterministic render path.
7. Run the Milestone 1 conformance scenarios and record the Pixel demo.

Voice-originated source requests may normalize only a small, documented set of
filename punctuation after an exact path lookup fails: `dash` or `hyphen`
between words and a terminal `dot M D`. The gateway must retain the original
tool arguments in its audit event, resolve the normalized candidate beneath an
explicit readable root, reject traversal and ambiguous case-insensitive
matches, and keep the canonical source bytes out of the model context.

An uncertain source reference must use `list_sources` before `read_source`.
Catalog enumeration is independently capability-scoped, lexically stable,
bounded by entry, scan, and depth limits, and returns relative Markdown names
without file contents, hidden entries, symlinks, or absolute host paths.

Ticket-sized commits should follow that order on
`epic/radcliffe-foundation`. The completed branch is reviewed and merged as one
major Milestone 1 PR.

### Smaller post-Milestone 1 PRs

After the vertical slice merges, use separate PRs for device identity and
secret management, mobile/network continuity, service operations and
observability, security/chaos/soak gates, additional source formats, and
mutating or web-connected tools.

## 13. Open questions

- Which frontier providers and local models belong in the first provider set?
- What exact capability presets should exist for personal workspace, project
  workspace, and read-only source access?
- What source formats are supported at launch: Markdown, plain text, PDF,
  HTML, EPUB, or others?
- Which Android versions and non-Pixel devices are in the release matrix?
- Is a public media edge acceptable for the first release’s privacy model, or
  must the initial deployment remain tailnet-only despite the reliability cost?
- At what point should the native client support offline queued text requests
  or downloaded source reading?
