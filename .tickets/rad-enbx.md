---
id: rad-enbx
status: open
deps: [rad-214n, rad-rwfl, rad-mp26, rad-4myy]
links: [rad-l1jc, rad-rwfl, rad-ghti, rad-9e2z]
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


## Notes

**2026-07-14T04:59:00Z**

2026-07-13 direction: port useful Oxford scenarios incrementally instead of waiting for the full session catalog. Milestone 1 core should automate content, latency, interruption, pause/resume, STT WER, failure recovery, and exact source rendering now; session-list/context-budget scenarios can remain dependency-gated.

**2026-07-14T05:26:25Z**

First Oxford-derived increment is now executable via make check-voice-local with retained acoustic and end-to-end evidence. Covered: multi-turn content/latency, pause suppression, noisy/echoed STT commands, and audible interruption. Remaining scenarios stay dependency-gated: rapid replacement/failure permutations, restart/catalog/context accounting, and exact-source randomized workspaces.

**2026-07-14T12:02:23Z**

2026-07-14 Oxford-derived external-suite increment now covers a live rapid replacement turn end to end, including active-stream observation, speech interruption, old-audio cessation, exact replacement content/latency, HTTP cancellation, durable interrupted disposition, and projection exclusion. Cold Ollama readiness is also deterministic: the Sidecar warms and pins the selected local model before health is exposed, preserving the unchanged five-second first-audio gate after restart.
