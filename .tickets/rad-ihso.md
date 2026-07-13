---
id: rad-ihso
status: open
deps: [rad-wnwh, rad-0ekb]
links: []
created: 2026-07-13T01:37:25Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, session]
---
# Implement conversation state machine, playback ledger, and reconnect recovery

Implement product-level state and client-authoritative playback acknowledgement above WebRTC transport recovery.

## Acceptance Criteria

The engine implements idle, connecting, listening, thinking, speaking, reading, paused, reconnecting, and failed states; every operation has session, generation, operation, and cancellation IDs; client acknowledgements persist started, position, completed, flushed, and failed segment events; reconnect resumes without duplicate or skipped output in the fixture corpus; interruptions create accurate context from heard versus unspoken segments; state and cursor tests pass under simulated reconnect and cancellation.
