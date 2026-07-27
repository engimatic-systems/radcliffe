---
id: rad-ihso
status: closed
deps: [rad-wnwh, rad-0ekb]
links: [rad-1w52]
created: 2026-07-13T01:37:25Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, session, milestone-1]
---
# Implement the Milestone 1 state machine and playback ledger

Implement the product-level state, cancellation generations, and client-authoritative playback acknowledgements required by the Milestone 1 conversation and deterministic-reading demo.

## Acceptance Criteria

The engine implements idle, connecting, listening, thinking, speaking, reading, paused, and failed states; every operation has session, generation, operation, and cancellation IDs; client acknowledgements persist started, position, completed, flushed, and failed segment events; interruption invalidates stale audible output within the target and records heard versus unspoken segments; pause/resume restarts at the persisted sentence boundary; end/restart does not leave an orphaned generation or tool call; and deterministic state/cursor tests pass with fake transports and providers.
