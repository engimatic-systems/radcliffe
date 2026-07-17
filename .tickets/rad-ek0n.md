---
id: rad-ek0n
status: in_progress
deps: [rad-twr3]
links: []
created: 2026-07-17T20:09:53Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-b8nm
tags: [radcliffe, operations, observability, telemetry, privacy]
---
# Correlate content-free turn telemetry and service metrics

Carry a safe trace identifier across Android, control plane, media worker, Pi sidecar, and Tool Gateway while exposing bounded operational counters and latency summaries.

## Acceptance Criteria

A server-generated trace ID is validated and propagated across HTTP and Protocol v2 boundaries for each turn without including message content or credentials; structured logs share trace, session pseudonym, generation, operation, phase, duration, and outcome fields; metrics cover readiness, first audio, interruption, reconnect, provider/tool errors, active work, and resource pressure; cardinality and redaction are bounded; an isolated cross-service test reconstructs one normal, interrupted, and failed turn solely from telemetry; and the operator can export a content-free diagnostic bundle.
