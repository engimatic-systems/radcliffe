---
id: rad-rwfl
status: in_progress
deps: [rad-214n]
links: [rad-lo0t, rad-chaa, rad-214n, rad-ghti, rad-nu8n]
created: 2026-07-14T02:47:05Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, milestone-2, voice, vad, endpointing]
---
# Serialize endpointed turns and tune natural-pause detection

Prevent a natural pause inside one spoken request from launching uncontrolled overlapping turns, while preserving responsive barge-in.

## Acceptance Criteria

At most one transcription/model turn owns the session at once; endpointing thresholds are configurable and documented; the dirty-joke exploratory sequence with a pause between clauses cannot yield two concurrent model completions; queued or merged follow-up speech follows a deterministic policy; agent playback does not become a new user transcript under the tested Pixel speaker conditions; Oxford-derived audio fixtures cover short pause, long pause, barge-in, background noise, and speaker echo; and latency remains within the Milestone 1 budget.


## Notes

**2026-07-14T03:03:23Z**

Core implementation on fix/generation-safe-voice-turns: endpoint task cancellation is awaited before replacement; default end silence increased from 600 ms to 900 ms; RMS, minimum voice, and silence thresholds are configurable; short-pause and sequential-ownership tests pass. Keep open pending Oxford audio fixtures, Pixel echo/speaker verification, and physical latency evidence.

**2026-07-14T04:05:38Z**

2026-07-13 Pixel evidence: live endpointing, pause/resume, interrupt, and end remained usable. Latest transcript-to-playback measurements exclude roughly 0.9s endpoint silence; phase-level latency instrumentation is tracked by rad-chaa.
