---
id: rad-2tft
status: open
deps: []
links: []
created: 2026-07-13T01:36:39Z
type: epic
priority: 1
assignee: coopwilliams
tags: [radcliffe, foundation, epic, milestone-1]
---
# Radcliffe foundation: reliable voice, provenance, and safe agent tools

Deliver a coherent vertical slice that proves the Radcliffe architecture on the target Pixel: native WebRTC voice, explicit session semantics, Pi-backed model/tool orchestration, and deterministic source reading through a safe read-only tool path.

## Acceptance Criteria

The epic is complete when all child tickets are closed and the documented Milestone 1 demo passes on the target Pixel: a user starts a native Android session, asks an ordinary voice question and hears a frontier-model answer within five seconds, interrupts audible output within 500 ms, invokes read_source through the Tool Gateway, hears canonical fixture text sent exactly to TTS with visible provenance, pauses and resumes from the persisted playback boundary, and ends/restarts the session without orphaned work. A reproducible demo report records latency, state, canonical-text, and tool-audit evidence.
