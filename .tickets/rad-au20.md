---
id: rad-au20
status: open
deps: [rad-wnwh, rad-avzf, rad-0ekb, rad-g7di]
links: []
created: 2026-07-13T01:37:25Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, tools, security]
---
# Implement the capability-scoped Tool Gateway

Build the sole execution path for model-requested tools, with immutable validation, authorization, durable audit, cancellation, and capability workers.

## Acceptance Criteria

Every tool request creates a CALL envelope before execution; gateway copies and validates arguments against Radcliffe schemas, evaluates the current capability grant, records authorization, and dispatches only permitted work; results and progress are persisted as CALL envelopes; initial tools default to sequential execution; cancellation reaches the worker and leaves an auditable final state; tests prove denied calls, path traversal, symlink escape, SSRF, stale grants, replay, and non-idempotent recovery fail safely.
