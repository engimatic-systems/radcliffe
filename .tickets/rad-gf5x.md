---
id: rad-gf5x
status: closed
deps: []
links: []
created: 2026-07-19T02:22:16Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-nhai
tags: [radcliffe, agent, jobs, lifecycle, sqlite, protocol]
---
# Persist the background agent job lifecycle contract

Define the restart-safe, idempotent control-plane foundation that lets run_job accept work immediately while foreground conversation turns continue independently.

## Design

Add typed job lifecycle and notification-policy values to Protocol v2, a durable SQLite job/event store with monotonic revisions, and bounded control-plane operations for accept, inspect, reprioritize, follow up, cancel, and claim. Keep private instructions in the separate job record and emit only bounded lifecycle metadata toward the conversation.

## Acceptance Criteria

run_job acceptance is idempotent and returns a job ID without waiting for execution; lifecycle state, notification policy, priority, budget metadata, follow-ups, cancellation intent, and bounded events survive restart; stale or conflicting mutations are rejected deterministically; one execution lease can own a queued job; private instructions and follow-up text are absent from conversation-facing events; and tests cover replay, concurrent claims, command precedence, cancellation, and database reopen.


## Notes

**2026-07-19T02:41:53Z**

Implemented the durable background-job lifecycle foundation: Protocol v2 JobCommand/JobEvent contracts; private SQLite job and follow-up context separated from replayable metadata-only events; immediate idempotent run_job acceptance; manage_job and enrolled-device status, follow-up, priority, and cancellation operations; single-owner internal execution leases with heartbeat delivery of stable follow-up IDs, expiry recovery, allowlisted result codes, and cancellation precedence; recovery inventory and operator documentation. Verification: make check passed (250 Python passed, 1 skipped; 41 sidecar tests passed; Android unit tests and lint passed).
