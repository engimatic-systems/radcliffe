---
id: rad-0ekb
status: open
deps: [rad-wnwh, rad-58mt]
links: []
created: 2026-07-13T01:37:25Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, provenance]
---
# Implement envelope persistence and deterministic projection engine

Build the append-only session store, immutable MessageEnvelope handling, RenderProjection, ContextProjection, and deterministic replay support.

## Acceptance Criteria

SQLite/WAL migrations create the event and playback tables; append and replay preserve envelope ordering and hashes; replaying a fixture with fixed projection versions yields identical RenderPlan and ContextPacket outputs; projections retain rule/version provenance; model summaries or interpretations are stored as new MODEL envelopes; unit and property tests cover cancellation, ordering, redaction, and replay.
