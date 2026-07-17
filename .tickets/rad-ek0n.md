---
id: rad-ek0n
status: closed
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

## Notes

**2026-07-17T21:03:42Z**

Implemented in 37faf7d. OperationRef now carries a validated server-generated trace ID through Protocol v2/Android playback acknowledgement, media-worker/Pi HTTP, and tool-gateway HTTP, with bounded legacy derivation for older Android acknowledgements. Media, inferred Android, sidecar, and control emit one exact content-free schema; metric dimensions exclude trace/session/operation labels. The private exporter drops malformed/extra-field events, strips operations details, scans configured secrets, and aggregates readiness, first audio, interruption, reconnect, provider/tool errors, active work, resource pressure, and durations. Full gate passed: 121 Python/1 skipped, 21 sidecar, Android unit/lint, mypy/TypeScript. Live local voice passed 37/37 plus STT WER 0.0; bundle reconstructed 12 traces across android/control/media_worker/sidecar (6 completed, 6 interrupted), invalid=0, active_work=0, with content/path scan clean. Isolated suite reconstructs completed/interrupted/failed traces and error/resource coverage. Managed-worker smoke passed all six checks. No cloud provider calls.
