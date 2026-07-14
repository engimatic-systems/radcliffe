---
id: rad-tx88
status: in_progress
deps: []
links: [rad-nmdx]
created: 2026-07-14T02:47:05Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, milestone-2, android, settings, security]
---
# Persist the Android local connection profile securely

Let the user configure the control URL and local bootstrap credential once, then reopen Radcliffe without retyping setup fields.

## Design

Persist non-secret preferences in Android DataStore and protect the bootstrap credential with an Android Keystore-backed boundary that can later migrate to enrolled device credentials under rad-nmdx.

## Acceptance Criteria

Control URL and credential survive activity recreation, process death, app restart, and device reboot; secret material is not stored in plaintext preferences, logs, backups, screenshots, or saved-instance state; failed connections do not erase valid configuration; Settings supports edit, test connection, and clear/reset; first-run and missing-credential states are explicit; tests cover persistence, migration, corruption, reset, and redaction; and the implementation has a documented migration path to device enrollment ticket rad-nmdx.


## Notes

**2026-07-14T03:35:26Z**

Implemented on feat/android-persisted-profile: DataStore stores normalized control URL and compatibility session; Android Keystore AES-256-GCM protects the bootstrap token in a versioned ciphertext envelope; setup/edit/test/reset UI added; screenshots and all backup/transfer domains disabled; unreadable credentials fail closed to setup; repository recreation, corruption, reset, normalization, and redaction unit tests added. Keep open pending physical upgrade, process-death, and reboot verification on the Pixel.
