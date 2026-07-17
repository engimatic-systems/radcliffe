---
id: rad-a40w
status: closed
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

## Notes

**2026-07-17T21:16:53Z**

Implemented owner-only bounded log retention, exact-content/credential/audio audit across current and rotated logs, stopped-service purge for protected legacy content, fail-closed privacy indexing, content-free source logging, dependency-safe worker startup retry, eight-condition recovery runbook, and isolated tabletop drill. Verification: make check (125 passed, 1 skipped; 21 Node tests; Android unit/lint), live revision 60ba6ca ready across all three supervised services, generated worker dispatch 6/6, audit clean, tabletop 4/4. No paid providers used and no phone gate required.
