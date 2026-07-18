---
id: rad-z6hl
status: closed
deps: []
links: []
created: 2026-07-17T00:52:27Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-nmdx
tags: [radcliffe, security, control-plane, enrollment]
---
# Add durable device enrollment and revocable API credentials

Replace shared-bootstrap authorization on user-facing control-plane routes with durable per-installation device credentials, while retaining the bootstrap secret only as the bounded enrollment authority.

## Acceptance Criteria

Enrollment is idempotent for one client-generated installation ID and high-entropy credential; only a credential digest is stored; session, catalog, timeline, context, and credential refresh routes require an active enrolled device; public clients cannot mint worker-role tokens or choose another device identity; operator revocation takes effect on the next request; enrollment, failed authentication, and revocation are content-free audit events; and tests cover idempotency, conflicting replay, invalid/revoked credentials, database restart, identity scope, and room-token scope/expiry.


## Notes

**2026-07-17T01:01:25Z**

2026-07-16 backend enrollment boundary implemented. The bootstrap bearer is limited to idempotent enrollment; user-facing session/catalog/timeline/context routes require an active per-device credential whose raw value is generated client-side and never stored or echoed. LiveKit client identity is selected from the enrolled record, worker-role credentials moved behind the internal service token, and revocation is immediate for control requests. Content-free audit rows cover enrollment, conflicts, unknown/revoked auth, and operator revocation. Evidence: make check passed 95 Python tests (1 skipped), 18 sidecar tests, Android unit/lint; isolated session conformance passed 29/29 at artifacts/session-conformance/20260717T010043Z/report.json.
