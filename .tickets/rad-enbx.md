---
id: rad-enbx
status: open
deps: [rad-214n, rad-rwfl, rad-mp26, rad-4myy]
links: [rad-l1jc]
created: 2026-07-14T02:48:09Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, milestone-2, testing, oxford, conformance]
---
# Port Oxford conversation and session scenarios into the Radcliffe external suite

Use the legacy Oxford suite as behavioral input for black-box Radcliffe coverage while keeping Protocol v2, provenance, and audio-first acceptance authoritative.

## Acceptance Criteria

A documented inventory maps useful Oxford scenarios to Radcliffe contracts without copying obsolete architecture; black-box tests cover normal multi-turn conversation, natural-pause endpointing, tap and speech interruption, rapid replacement turns, pause/resume, session restart, recent-session ordering, context accounting, exact source reading, and failure recovery; deterministic fakes cover race permutations and real local-provider runs remain separately labeled; seeds, audio, transcripts, envelope projections, state timelines, and latency are retained; the two anomalies from the 2026-07-13 Pixel run are permanent regressions; and the suite can run locally without GitHub Actions.

