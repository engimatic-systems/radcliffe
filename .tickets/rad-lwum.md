---
id: rad-lwum
status: open
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

