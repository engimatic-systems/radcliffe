---
id: rad-twr3
status: in_progress
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
