---
id: rad-l1jc
status: open
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
