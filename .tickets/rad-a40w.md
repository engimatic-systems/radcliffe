---
id: rad-a40w
status: in_progress
deps: [rad-twr3, rad-ek0n, rad-qz6l]
links: []
created: 2026-07-17T20:10:11Z
type: task
priority: 2
assignee: coopwilliams
parent: rad-b8nm
tags: [radcliffe, operations, logs, runbook, privacy]
---
# Enforce log retention redaction and operator recovery runbooks

Bound local operational data and document routine failure recovery once service packaging, readiness, telemetry, and backup paths exist.

## Acceptance Criteria

Every service log has documented rotation, retention, file-mode, and deletion behavior; automated scans reject authorization values, device/provider credentials, message bodies, source text, and microphone audio in default logs and diagnostic bundles; runbooks cover unhealthy dependencies, stuck workers, model cold start, LiveKit disconnect, database lock/corruption, lost device credential, upgrade rollback, and disk pressure; each procedure names observable success criteria and escalation limits; and a tabletop script executes the safe checks against isolated fixtures.
