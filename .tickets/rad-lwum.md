---
id: rad-lwum
status: closed
deps: [rad-z6hl, rad-zbnv]
links: []
created: 2026-07-17T00:52:27Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-nmdx
tags: [radcliffe, security, testing, enrollment]
---
# Add device-auth rotation and adversarial integration conformance

Exercise the complete enrollment, authentication, refresh, expiration, revocation, and replay lifecycle independently of the control-plane implementation.

## Acceptance Criteria

A black-box suite proves first enrollment, retry, conflicting replay rejection, session-token refresh, room/identity/TTL scope, expired and revoked denial, audit redaction, control-plane restart, credential rotation, and bootstrap rejection on ordinary session APIs; artifacts contain no raw bootstrap, device, LiveKit, or provider secret; and the security runbook documents recovery and rotation.


## Notes

**2026-07-17T03:12:39Z**

Implemented atomic self-service device credential rotation, content-free internal audit access, schema migration 006, and an isolated 19-check black-box suite covering enrollment retry/replay, bootstrap confinement, signed room-token identity/room/source/TTL and expiry, refresh, two restarts, rotation/collision/revocation, audit redaction, and exact raw-secret artifact scanning. Evidence: artifacts/device-auth-conformance/20260717T031131Z/report.json passed 19/19; make check passed 99 Python tests (1 skipped), 18 sidecar tests, mypy, Ruff, Android unit tests, and lint. Added docs/security-operations.md; paid providers remained disabled and no cloud calls were made.

**2026-07-17T03:14:56Z**

Deployed commit a0d0252 locally after a SQLite-consistent pre-migration backup. Live health and internal content-free audit endpoints returned HTTP 200, schema version is 6, and post-restart worker-dispatch conformance passed all 5 checks with clean session teardown.
