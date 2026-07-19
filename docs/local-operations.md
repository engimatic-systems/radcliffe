# Radcliffe local operations

This runbook operates the private single-owner macOS profile without enabling
cloud providers. The local Ollama, Whisper, and TTS configuration remains the
default; having funded provider keys in `var/.env` does not authorize a paid
request.

The installed LaunchAgent calls `scripts/run-local-sidecar.sh`, which forces
Ollama and removes cloud credentials even if live-debug settings are present.
Cloud testing uses the separate, manually started
`scripts/run-live-sidecar.sh` path, never an operations fallback. Its explicit
opt-in, disclosure, budgets, and paid-smoke gate are documented in the root
README and sidecar README.

## Install and manage services

Keep `var/.env` mode `0600`, then install the versioned LaunchAgents:

```sh
make services-install-local
```

The installer validates the workspace and protected environment, atomically
writes owner-only plists under `~/Library/LaunchAgents`, reloads services in
dependency order, and is safe to run again. Plists contain executable and log
paths only—never environment values, provider keys, bootstrap/internal tokens,
or LiveKit signing secrets.

The default make target includes the fixed-room compatibility worker used by
the legacy `radcliffe-demo` room. Server-generated Android conversations use
control-plane-managed per-session workers instead. To omit the compatibility
worker, invoke `scripts/radcliffe_services.py` without
`--include-compat-worker`.

Common commands:

```sh
make services-status-local
make services-restart-local
uv run python scripts/radcliffe_services.py stop --include-compat-worker
uv run python scripts/radcliffe_services.py start --include-compat-worker
uv run python scripts/radcliffe_services.py uninstall --include-compat-worker
```

Install, start, stop, restart, and uninstall are idempotent. Restart stops in
reverse dependency order and starts the sidecar before the control plane and
compatibility worker.

## Liveness and readiness

The control plane exposes two content-free probes:

- `GET /healthz` proves the HTTP process completed application startup.
- `GET /readyz` returns HTTP 200 only when operational dependencies are usable;
  otherwise it returns HTTP 503 with stable component and reason codes.

Readiness checks contain no endpoint, path, credential, prompt, transcript,
source text, or exception string. Current components and failure codes are:

| Component | Healthy code | Failure codes |
| --- | --- | --- |
| Database | `schema_current` | `database_missing`, `database_unavailable`, `integrity_failed`, `schema_mismatch` |
| Pi sidecar/Ollama | `sidecar_ready` | `sidecar_unavailable` |
| LiveKit signal | `signal_reachable` | `livekit_url_invalid`, `signal_unavailable` |
| Source root | `source_root_readable` | `source_root_unavailable` |
| Worker supervisor | `supervisor_running` | `supervisor_unavailable`, `maintenance_stopped` |
| Background-job executor | `job_executor_running` | `job_executor_unavailable`, `job_executor_stopped` |

Managed workers deliberately report `managed_workers_disabled` when disabled
in an isolated test profile; this is not a readiness failure.

## Operational snapshot

Run the live dependency gate and retain a private JSON snapshot:

```sh
make check-operations-local
```

The report is written to `artifacts/operations/status.json` with mode `0600`.
It includes only service state/PIDs, sanitized endpoint authorities, repository
revision, tool versions, readiness codes, database schema/integrity and size,
disk capacity, and aggregate log sizes. Before emission, the reporter compares
the serialized bytes with configured token, secret, API-key, and credential
values and fails closed if any appear.

Use `scripts/run-local-ops.sh --json` for stdout JSON or
`scripts/run-local-ops.sh --wait 60` during a cold Ollama start. A nonzero exit
means at least one required component is not ready; the legacy compatibility
worker is reported but optional for generated Android sessions.

Before handing an APK to a phone tester for background-job work, run:

```sh
make check-background-jobs-local
```

This content-free gate compares the running control plane and sidecar revisions
to the checkout, requires the live sidecar to advertise `run_job` and
`manage_job`, requires the control executor to be running, inspects the
configured Colima image, and drives a synthetic job through the real JSON-lines
process contract without invoking any model provider. A revision or capability
failure requires rebuilding the sidecar and restarting local services; an
executor or image failure requires rebuilding/configuring the job sandbox
before restart. Do not hand off an APK until this gate and
`make check-operations-local` both pass.

For a content-free correlated trace/metrics bundle, run
`make export-diagnostics-local`. Its bounded schema and redaction rules are in
[`telemetry-operations.md`](telemetry-operations.md).

Log retention, the exact-content privacy audit, and condition-specific recovery
limits are documented in [`operator-recovery.md`](operator-recovery.md).

## Safe first response

1. Run `make services-status-local` to distinguish unloaded processes from
   dependency failures.
2. Run `scripts/run-local-ops.sh --json` and act on the stable failure code.
3. For `sidecar_unavailable`, verify Ollama is running and the configured model
   is installed before restarting the sidecar. Do not switch to a cloud model.
4. For `signal_unavailable`, inspect the local LiveKit container and LAN path;
   do not change Android credentials.
5. For database or schema failures, stop mutation and preserve the database.
   Do not copy it into an ordinary diagnostic bundle because it contains
   conversation content.
6. After correction, use `make services-restart-local`, then require both
   `make check-operations-local` and `make check-worker-dispatch-local` to pass.

Database backup/restore and revision rollback are covered by
[`database-recovery.md`](database-recovery.md). The operational children of
`rad-b8nm` are locally implemented; physical-network foundation gates remain
tracked separately.
