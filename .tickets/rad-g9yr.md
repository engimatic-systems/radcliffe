---
id: rad-g9yr
status: open
deps: [rad-2tft]
links: [rad-ig6k, rad-0tmq]
created: 2026-07-13T01:52:53Z
type: feature
priority: 1
assignee: coopwilliams
tags: [radcliffe, post-m1, android, reliability, operator-interactive]
---
# Harden Android background, Bluetooth, and network continuity

Expand the single-Pixel speaker demo into dependable locked-screen, Bluetooth, and changing-network operation.

## Acceptance Criteria

The session is verified with speaker, wired headset, and Bluetooth; notification and hardware controls work while locked; audio focus and device-route changes recover correctly; Wi-Fi-to-cellular, cellular-to-Wi-Fi, and temporary network loss preserve session intent; TURN-only operation passes; reconnect does not duplicate or skip acknowledged speech; Android process recreation restores safe UI/session state; and physical-device evidence is retained.
