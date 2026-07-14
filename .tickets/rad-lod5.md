---
id: rad-lod5
status: closed
deps: []
links: [rad-0ekb, rad-mp26, rad-yk82, rad-h2lv]
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


## Notes

**2026-07-14T05:37:24Z**

Implemented on feat/durable-session-catalog. Added incremental SQLite migration 002, collision-resistant server IDs, principal-scoped durable metadata, idempotency keys, deterministic cursor pagination, detail/update/reopen APIs, activity triggers for envelopes/playback/tools, and fresh nonce-bearing least-privilege tokens. Eleven focused API/database tests cover v1 migration, restart, authorization scope, stable ordering, pagination, explicit compatibility IDs, token grants, and 12-way concurrent creation. Full make check passes (47 Python, 7 sidecar, Android unit/lint). The running control plane migrated schema versions 1->2 and successfully cataloged/reopened radcliffe-demo.
