---
id: rad-zbnv
status: in_progress
deps: [rad-z6hl]
links: []
created: 2026-07-17T00:52:27Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-nmdx
tags: [radcliffe, security, android, enrollment]
---
# Migrate Android from bootstrap secret to enrolled device identity

Use the existing Keystore-backed profile boundary to generate and retain a per-installation credential, enroll once with the compatibility bootstrap secret, then stop using and remove that bootstrap secret.

## Acceptance Criteria

Android generates a stable installation ID and 256-bit credential locally; enrollment and lost-response retry are idempotent; the device credential and installation state are Keystore-protected; the bootstrap token is deleted after enrollment; existing profiles migrate without re-entry; revoked and corrupt credentials return to an explicit re-enrollment state without leaking secrets; and unit plus Pixel upgrade/relaunch tests pass.


## Notes

**2026-07-17T01:16:14Z**

Local Android migration is implemented. The app creates and encrypts its credential before enrollment, reuses it after lost responses or process recreation, removes the bootstrap token after completion, invalidates rejected credentials into explicit re-enrollment, and migrates legacy saved bootstrap profiles automatically. make check is green (95 Python, 18 sidecar, Android unit/lint); strict-auth voice conformance passed 35/35 and managed-worker dispatch passed 5/5. Remaining acceptance checkpoint: install v0.5 over the existing Pixel app and verify enrollment plus relaunch without re-entering configuration.
