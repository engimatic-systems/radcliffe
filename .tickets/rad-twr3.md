---
id: rad-twr3
status: closed
deps: []
links: []
created: 2026-07-17T20:09:45Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-b8nm
tags: [radcliffe, operations, supervision, readiness, local]
---
# Package supervised local services and readiness diagnostics

Replace machine-local launch setup with reproducible repository-owned service definitions and a content-free diagnostic snapshot for the control plane, Pi sidecar, local model runtime, LiveKit, database, and worker supervisor.

## Acceptance Criteria

A clean macOS local profile can install, start, stop, restart, and inspect versioned control-plane, sidecar, and compatibility-worker LaunchAgents without embedding secrets in plist files; scripts resolve paths safely and fail closed when the protected environment is absent; shallow liveness and dependency-aware readiness distinguish database, sidecar/Ollama, LiveKit, and worker failures; one command emits a redacted JSON and human report with process state, versions, endpoints, database schema/integrity, disk/log sizes, and actionable failure codes; install/uninstall are idempotent; tests use isolated fixtures/fakes and a local smoke proves restart recovery without paid providers.

## Notes

**2026-07-17T20:27:51Z**

Implemented in 57041c5 and 7de980f. Full gate passed (110 Python tests/1 skipped, 18 sidecar tests, TypeScript, Android unit/lint). Installed owner-only LaunchAgents twice, reproduced and fixed the bootout/bootstrap race, then restarted all services. Clean-revision operations report was ready with sidecar/control/compat worker, control /readyz, Ollama-warmed sidecar, LiveKit, SQLite schema 6/integrity, source root, managed supervisor, and disk checks green; output and plists were mode 0600 with no environment values. Generated-session smoke passed exactly-one-worker join, idempotent dispatch, status redaction, cleanup, and archive recovery without cloud providers.
