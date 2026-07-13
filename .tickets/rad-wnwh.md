---
id: rad-wnwh
status: closed
deps: []
links: []
created: 2026-07-13T01:37:25Z
type: task
priority: 0
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, protocol, milestone-1]
---
# Define Protocol v2 and the MessageEnvelope schema

Specify the versioned cross-process protocol for sessions, envelopes, state transitions, playback acknowledgements, tool calls, source provenance, errors, and reconnects. Produce the Protobuf schema and compatibility rules.

## Acceptance Criteria

Protocol v2 has a reviewed Protobuf schema; MessageEnvelope represents user, model, call, text-source, and system items with stable IDs, sequence, hash, provenance, metadata, and sensitivity; state and reconnect messages are versioned and idempotent; compatibility and schema-evolution rules are documented; generated Kotlin and Python bindings compile.
