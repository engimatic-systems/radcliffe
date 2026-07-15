---
id: rad-lod5
status: open
deps: []
links: [rad-0ekb]
created: 2026-07-14T02:47:05Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, milestone-2, control-plane, sessions, api]
---
# Add a durable control-plane session catalog with server-generated IDs

Move session identity and recency from a manually typed Android field into a durable, authenticated control-plane catalog.

## Acceptance Criteria

Creating a session without an ID returns a collision-resistant server-generated identifier; explicit IDs remain supported only where required for compatibility and tests; session metadata persists across control-plane restarts; list and detail endpoints are scoped to the authorized device or user; the list is paginated and ordered by last activity descending with a deterministic tie-break; records expose created time, last activity, optional title, lifecycle status, and protocol version without leaking message contents; reopening issues a fresh least-privilege room token; idempotent creation does not duplicate sessions; and API/database tests cover ordering, pagination, authorization, restart, and concurrent creation.

