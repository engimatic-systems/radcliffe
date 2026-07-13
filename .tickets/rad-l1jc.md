---
id: rad-l1jc
status: open
deps: [rad-wnwh, rad-8i08, rad-h2lv, rad-ihso, rad-g7di, rad-au20]
links: []
created: 2026-07-13T01:37:26Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, testing]
---
# Establish the independent voice conformance and chaos test suite

Port Oxford's audio-first acceptance philosophy into a Protocol v2 test suite that runs on isolated capacity and covers mobile reliability as well as agent behavior.

## Acceptance Criteria

The suite runs separately from the system under test or with isolated compute capacity; a reference implementation validates the harness; tests judge heard audio and cross-check state; coverage includes first-audio latency, barge-in, pause/resume, exact source reading, quotation, tool lifecycle, playback recovery, and reconnect; network-chaos scenarios cover packet loss, TURN-only, and worker restart; reports retain reproducible seeds and artifacts.
