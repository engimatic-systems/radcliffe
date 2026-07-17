---
id: rad-b8nm
status: in_progress
deps: [rad-2tft]
links: []
created: 2026-07-13T01:52:53Z
type: feature
priority: 1
assignee: coopwilliams
tags: [radcliffe, post-m1, operations, observability]
---
# Operationalize Radcliffe services and end-to-end observability

Package and operate the control plane, media worker, Pi sidecar, tool workers, and LiveKit deployment with correlated telemetry and safe recovery.

## Acceptance Criteria

All services have reproducible packages and supervised startup; health/readiness checks distinguish LiveKit, speech providers, Pi, database, and tool-worker failures; one trace ID follows a turn across Android, Python, Pi, and Tool Gateway without capturing sensitive content by default; latency, interruption, reconnect, error, and resource metrics are emitted; logs have retention/redaction rules; SQLite and configuration backups restore successfully; upgrade and rollback procedures are tested; and an operator runbook covers common failures.
