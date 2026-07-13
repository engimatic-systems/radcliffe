---
id: rad-f4j2
status: open
deps: [rad-2tft, rad-nmdx, rad-g9yr, rad-b8nm]
links: []
created: 2026-07-13T01:52:53Z
type: feature
priority: 1
assignee: coopwilliams
tags: [radcliffe, post-m1, testing, reliability, security]
---
# Expand conformance with security, chaos, and soak release gates

Turn the Milestone 1 focused suite into production release gates across hostile inputs, network failures, provider failures, and long-running use.

## Acceptance Criteria

The suite adds prompt-injection, unauthorized-tool, traversal, symlink, SSRF, archive, stale-token, and replay cases; network scenarios include packet loss, reordering, TURN-only, and worker/server restarts; provider timeouts and malformed outputs fail safely; an eight-hour soak detects leaks, deadlocks, orphaned processes, and session drift; latency is measured without evaluator resource contention; and CI or a documented release job blocks release on failed deterministic gates.
