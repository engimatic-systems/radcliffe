---
id: rad-8i08
status: in_progress
deps: [rad-wnwh, rad-58mt]
links: []
created: 2026-07-13T01:37:25Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, webrtc, milestone-1]
---
# Provision the self-hosted LiveKit Milestone 1 media topology

Create the reproducible single-node LiveKit/TURN/token-service topology needed to run and diagnose the Milestone 1 Pixel demo.

## Acceptance Criteria

A self-hosted LiveKit server is reachable from the target Pixel and Radcliffe worker through trusted TLS; direct UDP and at least one verified TURN fallback path work; the token service issues least-privilege short-lived room tokens without exposing server secrets; health and ICE/TURN diagnostics are documented; a clean bootstrap reproduces the topology; and the worker connects outbound without opening inbound access to the home machine.

## Notes

**2026-07-13T03:02:56Z**

Implementation is committed: pinned host-network LiveKit/TURN profiles, configuration generator, diagnostics, scoped token service, and tests. Remaining acceptance evidence requires a public TLS domain, Docker host, direct-UDP run, and forced TURN run from the target Pixel.

**2026-07-13T03:54:22Z**

LAN development topology is now deployed locally: pinned LiveKit is reachable at ws://192.168.18.71:7880, scoped tokens are issued by the control plane, and the supervised media worker connects outbound to radcliffe-demo. Trusted TLS, public TURN, forced-relay, and target-Pixel evidence remain intentionally deferred.

**2026-07-20T04:15:22Z**

Correction as of 2026-07-19: ws://192.168.18.71:7880 describes the earlier LAN-only deployment and is not the current phone-facing signaling URL. Current room credentials advertise wss://ravd-64-mbp.tail2aca23.ts.net:8089; Tailscale Serve exposes that endpoint tailnet-only and proxies it to local LiveKit at http://127.0.0.1:7880. Port 7880 is not directly exposed through Tailscale Serve.
