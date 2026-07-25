---
id: rad-au20
status: closed
deps: [rad-wnwh, rad-avzf, rad-0ekb]
links: [rad-1w52]
created: 2026-07-13T01:37:25Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, tools, security, milestone-1]
---
# Implement the Milestone 1 read-only Tool Gateway

Build the sole execution path for the Milestone 1 read_source tool, with immutable validation, a read-only capability root, durable audit, cancellation, and exact source handoff to RenderProjection.

## Acceptance Criteria

Every read_source request creates a CALL envelope before execution; the gateway copies and validates arguments against the Radcliffe schema, evaluates a read-only root grant, records authorization, and resolves only permitted Markdown fixtures; canonical bytes and content hash are persisted in a TEXT_SOURCE/CALL result envelope and handed directly to RenderProjection; execution is sequential; cancellation leaves an auditable final state; and tests cover allowed reads, denied paths, traversal, symlink escape, stale grants, duplicate call IDs, and exact canonical-text output.
