---
id: rad-l1jc
status: in_progress
deps: [rad-wnwh, rad-8i08, rad-h2lv, rad-ihso, rad-g7di, rad-au20, rad-ghti]
links: []
created: 2026-07-13T01:37:26Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, testing, milestone-1]
---
# Establish the Milestone 1 audio conformance suite and demo report

Port Oxford's audio-first acceptance philosophy into a focused Protocol v2 suite that verifies the Milestone 1 Pixel demo and produces reproducible evidence.

## Acceptance Criteria

The suite runs separately from the system under test or with isolated compute capacity; a reference implementation validates the harness; tests judge heard audio and cross-check state; the required scenarios cover a normal answer within five seconds, audible stop within 500 ms, pause/resume from a persisted boundary, a read_source CALL lifecycle, and exact canonical source text submitted to TTS; reports retain seeds, audio, envelope/state timelines, tool audit, and measured checks; and a documented Pixel demo procedure produces the same report bundle.

## Notes

**2026-07-13T03:02:56Z**

Credential-free contract report is implemented and passing with exact source/TTS hashes. Ticket remains open because the acceptance authority requires captured Pixel audio, isolated latency, TURN, interruption, and pause/resume evidence.

**2026-07-13T04:37:37Z**

Added scripts/livekit-voice-smoke.py as the first external live-room harness toward the Oxford-style suite. It publishes generated microphone audio through LiveKit, checks pause suppression, captures returned TTS audio, validates binary state/message events, enforces <5s first audio and <500ms interrupt acknowledgement. Current local run: 2.812s first audio, 0.051s interrupt acknowledgement.

**2026-07-14T02:46:33Z**

Physical exploratory run confirms the basic Pixel voice/control loop. It also supplied two valuable regression scenarios for the external suite: interrupted unanswered prompts must not bleed into the next projection, and a mid-utterance endpoint split must not create overlapping model completions or invalid state transitions.
