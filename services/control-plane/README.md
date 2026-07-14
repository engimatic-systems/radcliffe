# Radcliffe control plane

The control plane owns the durable single-user session catalog and issues
short-lived, room-scoped LiveKit credentials. All `/v1/sessions` routes require
the configured bootstrap bearer token. The authenticated principal comes from
`RADCLIFFE_OWNER_ID`; clients cannot select or override it.

## Session API

- `POST /v1/sessions` creates a session and returns client or worker room
  credentials. `session_id` is optional; omitting it creates a collision-resistant
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

Catalog rows and envelope events share the configured SQLite/WAL database.
Envelope, playback, and tool-call inserts advance session recency through
database triggers, so ordering survives worker and control-plane restarts.
Schema migrations are incremental and recorded in `schema_migrations`.

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
