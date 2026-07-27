# Radcliffe local operations

This runbook operates the private single-owner macOS profile. Deterministic
development keeps cloud providers disabled, while every operator/user acceptance
run explicitly activates pinned Haiku for foreground conversation and an approved,
locally pinned Anthropic background executor. Qwen is retained only as a deprecated
development selector.

`make services-install-local` installs the deprecated local-only sidecar and can
be used for deterministic development. It must not be used for user acceptance.
`make services-install-user-test` installs the separate pinned Haiku runner and
an owner-visible, non-secret cloud opt-in in the LaunchAgent; provider credentials
remain only in protected `var/.env`.

## Install and manage services

Keep `var/.env` mode `0600`, then install the versioned LaunchAgents:

```sh
make services-install-local
make services-install-user-test  # required for operator/user acceptance
```

The installer validates the workspace and protected environment, atomically
writes owner-only plists under `~/Library/LaunchAgents`, reloads services in
dependency order, and is safe to run again. Plists contain executable and log
paths plus the non-secret profile opt-in only—never provider keys,
bootstrap/internal tokens, or LiveKit signing secrets.

The default make target includes the fixed-room compatibility worker used by
the legacy `radcliffe-demo` room. Server-generated Android conversations use
control-plane-managed per-session workers instead. To omit the compatibility
worker, invoke `scripts/radcliffe_services.py` without
`--include-compat-worker`.

Common commands:

```sh
make services-start-local
make services-status-local
make services-restart-local
make services-stop-local
uv run python scripts/radcliffe_services.py uninstall --include-compat-worker
```

Install, start, stop, restart, and uninstall are idempotent. Restart stops in
reverse dependency order and starts the sidecar before the control plane and
compatibility worker. The lifecycle Make targets print status and the one
launchd-managed PID for each running service. A repeated start cannot create a
second managed instance because launchd admits only one loaded job per service
label; restart also waits for each old PID to exit before returning.

Starting, restarting, or stopping the backend does not change the bootstrap
token, the phone's enrolled device credential, or the existing Tailscale Serve
configuration. Once the backend is started again, the Android app should use
the same configured HTTPS origin.

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

For a phone connecting from outside the Mac's LAN, expose the loopback control
plane on a dedicated tailnet-only HTTPS port without replacing unrelated Serve
handlers:

```sh
tailscale serve --bg --https=8090 http://127.0.0.1:8000
curl --fail https://<machine>.<tailnet>.ts.net:8090/healthz
```

Configure Android with that exact HTTPS origin, not the Mac's LAN address and
not another Tailscale Serve port. Changing origins deliberately requires device
re-enrollment with the protected bootstrap token. A successful host-side curl
does not prove the phone route: before declaring the checkpoint passed, require
the app's Connection test and Recent conversations load to succeed over the
intended phone network.

Voice joining is a separate network gate from the control API. The
`livekit_url` returned in newly issued room credentials must also be reachable
from the phone, and LiveKit must advertise an allowed tailnet ICE/TCP or ICE/UDP
port rather than a LAN address. Require a newly created conversation to join
and exchange audio before declaring the complete phone path ready.

Operator-review APKs use the non-debuggable `review` build type. It excludes
Compose debug tooling, rejects cleartext control-plane URLs, and removes LiveKit's
unused camera, media-projection, and screen-capture declarations while retaining
its microphone, data-channel, and remote-audio paths. Review signing credentials
are supplied only through `RADCLIFFE_ANDROID_REVIEW_KEYSTORE`,
`RADCLIFFE_ANDROID_REVIEW_STORE_PASSWORD`, `RADCLIFFE_ANDROID_REVIEW_KEY_ALIAS`,
and `RADCLIFFE_ANDROID_REVIEW_KEY_PASSWORD`; neither the keystore nor its
credentials belongs in the repository. Inspect the merged APK manifest and
signature before copying it to the operator share.

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
