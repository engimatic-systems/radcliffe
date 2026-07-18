---
id: rad-nmdx
status: in_progress
deps: [rad-2tft]
links: [rad-tx88]
created: 2026-07-13T01:52:53Z
type: feature
priority: 1
assignee: coopwilliams
tags: [radcliffe, post-m1, security, identity]
---
# Implement device enrollment, control-plane authentication, and secret storage

Turn the single-user demo token service into an authenticated control plane with revocable device identity and managed service/provider credentials.

## Acceptance Criteria

A device must enroll before receiving a room token; enrolled-device credentials are stored using Android platform protection and can be revoked server-side; room tokens are short-lived and scoped to one participant/session; provider, LiveKit, and TURN secrets are stored outside source control with documented rotation; failed and revoked authentication attempts are auditable; and integration tests cover enrollment, token refresh, expiration, revocation, and replay.
