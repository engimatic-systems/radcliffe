# Radcliffe control plane

The control plane owns the durable single-user session catalog and issues
short-lived, room-scoped LiveKit credentials. The configured bootstrap bearer is
accepted only by `POST /v1/devices/enroll`; ordinary `/v1/sessions` routes require
an active per-installation device credential. The authenticated owner comes from
`RADCLIFFE_OWNER_ID`, and the server—not the request—selects the enrolled
participant identity.

## Device API

- `POST /v1/devices/enroll` accepts a stable installation ID and a client-generated
  256-bit `rdc_dev_...` credential under bootstrap authorization. Retrying the
  identical pair is safe; changing either side of an existing binding conflicts.
  Only the SHA-256 credential digest is stored, and the response never echoes the
  secret.
- `GET /v1/devices/me` verifies the device credential and returns non-secret
  enrollment metadata.
- `POST /v1/devices/{device_id}/revoke` is an operator-only route protected by
  the internal service token. Revocation denies the next control request; an
  already issued room token remains bounded by its short TTL.

Enrollment, conflict, failed-authentication, and revocation events are recorded
without request bodies, credentials, or conversational content.

## Health API

- `GET /healthz` is shallow liveness and reports when application startup has
  completed.
- `GET /readyz` verifies the current SQLite schema and quick integrity check,
  Pi sidecar/Ollama readiness, LiveKit signaling, source-root readability, and
  managed-worker maintenance. It returns HTTP 503 when a required component is
  unavailable and exposes stable content-free reason codes only.

## Session API

- `POST /v1/sessions` creates a session and returns client room credentials bound
  to the enrolled device. `session_id` is optional; omitting it creates a collision-resistant
  `ses_...` identifier. Supply a stable `idempotency_key` when retrying automatic
  creation. Explicit IDs remain available for the compatibility demo and tests.
- `GET /v1/sessions?limit=20&cursor=...` lists metadata newest-first. The opaque
  cursor preserves a deterministic `(last_activity_at, session_id)` boundary.
  Archived sessions are hidden unless `include_archived=true`, keeping test and
  deliberately archived conversations out of the normal Android home.
- `GET /v1/sessions/{session_id}` returns metadata only. Envelope/message content
  is deliberately absent.
- `GET /v1/sessions/{session_id}/context-usage` replays the current deterministic
  projection and returns model ID, exact/estimated input usage, reserved output,
  remaining capacity, percentage, projection version, and overflow state. It
  never returns message content or the internal request digest.
- `GET /v1/sessions/{session_id}/messages?after=...&limit=...` returns the
  owner-authenticated, chronological persisted timeline for reopen. Secret
  envelopes are skipped without breaking the stable sequence cursor.
- `PATCH /v1/sessions/{session_id}` updates the optional title and lifecycle
  status (`active`, `ended`, or `archived`).
- `POST /v1/sessions/{session_id}/credentials` reopens a durable session with a
  fresh least-privilege room token.
- `POST /v1/internal/sessions/{session_id}/worker-credentials` is the only HTTP
  path that can mint a worker-role token and requires the internal service token.

Catalog rows and envelope events share the configured SQLite/WAL database.
Envelope, playback, and tool-call inserts advance session recency through
database triggers, so ordering survives worker and control-plane restarts.
Schema migrations are incremental and recorded in `schema_migrations`.

## Background job API

- `run_job` on `POST /v1/tool-calls` durably accepts a private job instruction
  and immediately returns a job ID. `manage_job` inspects, follows up,
  reprioritizes, or cancels it.
- `GET /v1/sessions/{session_id}/jobs`, `GET /v1/jobs/{job_id}`, and
  `GET /v1/jobs/{job_id}/events` expose owner-authenticated metadata and
  privacy-minimized replayable events. They never return instructions or
  follow-up text.
- `POST /v1/jobs/{job_id}/commands` gives an enrolled device the same bounded,
  idempotent mutation surface.
- `POST /v1/internal/jobs/claim`, `/heartbeat`, and `/events` form the internal
  execution lease. Claim returns the primary instruction and known follow-ups;
  heartbeat returns stable-ID follow-ups for exactly-once delivery by the
  executor. All three routes require the internal service token.

Job revisions, events, follow-ups, cancellation intent, and leases share the
main SQLite/WAL database. An expired lease is recoverable after restart, while
a current or cancellation-requested job cannot be completed by a stale owner.

### Opt-in executor process

Durable acceptance does not grant host execution by itself. The executor is
disabled by default and starts only with all of:

```sh
RADCLIFFE_JOB_EXECUTOR_ENABLED=1
RADCLIFFE_JOB_EXECUTOR_COMMAND='/absolute/path/to/user-sandbox-wrapper --json-lines'
RADCLIFFE_JOB_WORKSPACE_ROOT=/absolute/path/to/private-job-workspaces
```

The command string is split into an argv without a shell. The wrapper is the
user-owned containment boundary (for example a disposable VM or container);
the control-plane adapter does not pretend that a scrubbed host subprocess is
an OS sandbox. It passes no Radcliffe, provider, LiveKit, GitHub, or host-home
credential. Each attempt receives a mode-0700 working directory used as its
`HOME`, private temporary/XDG roots, and a mode-0600 `executor.jsonl`
transcript. The primary instruction and stable-ID follow-ups travel as JSON
lines over stdin, never argv or environment.

Input frames are `start` and `follow_up`. Stdout/stderr is one bounded JSON-line
channel and may emit only `event` frames with an allowlisted kind/detail code
and bounded entity/artifact IDs. Arbitrary stdout, malformed frames, or output
past `RADCLIFFE_JOB_EXECUTOR_MAX_OUTPUT_BYTES` fail the attempt without entering
the conversation. `RADCLIFFE_JOB_EXECUTOR_HEARTBEAT_SECONDS` must be shorter
than `RADCLIFFE_JOB_EXECUTOR_LEASE_SECONDS`. Runtime expiry terminates the whole
process group; committed user cancellation wins over a late success. A
control-plane restart terminates owned processes and lets their durable leases
expire for an explicit recovered attempt.

## Managed workers

Server-generated sessions use a control-plane-owned local worker supervisor;
explicit compatibility sessions such as `radcliffe-demo` remain external. The
supervisor starts at most one media-worker process per active generated session,
reconciles recent sessions after a graceful control-plane restart, stops workers
when a session ends or exceeds `RADCLIFFE_WORKER_IDLE_SECONDS`, and stores only a
safe lifecycle status in catalog responses. Child environments exclude the
bootstrap token, LiveKit API secret, and disabled cloud-provider keys. Per-session
logs live under `RADCLIFFE_WORKER_LOG_DIRECTORY` (default `var/workers`).
Compatibility sessions report `external` rather than pretending that the
control-plane supervisor can observe their separately launched worker.

Context accounting is delegated over loopback to
`RADCLIFFE_SIDECAR_URL` (default `http://127.0.0.1:8787`) so the count is taken
at the same Pi/provider serialization boundary as generation.

Run the real local dispatch check after LiveKit, the sidecar, and the control
plane are healthy:

```sh
make check-worker-dispatch-local
```
