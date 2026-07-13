---
id: rad-8i08
status: open
deps: [rad-wnwh, rad-58mt]
links: []
created: 2026-07-13T01:37:25Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, webrtc]
---
# Provision the self-hosted LiveKit development media edge

Create the deployable LiveKit/TURN/token-service development topology and operational checks needed for reliable Android connectivity.

## Acceptance Criteria

A self-hosted LiveKit server is reachable through a trusted TLS domain; direct UDP, TURN/UDP, TURN/TLS, and TCP fallback are configured and documented; the token service issues least-privilege short-lived room tokens without exposing server secrets; health, ICE/TURN diagnostics, and a TURN-only test path exist; the Radcliffe worker connects outbound without opening inbound access to the home machine.
