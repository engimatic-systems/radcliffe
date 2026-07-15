---
id: rad-rwfl
status: in_progress
deps: [rad-214n]
links: [rad-lo0t, rad-chaa, rad-214n, rad-ghti, rad-nu8n, rad-l1jc, rad-enbx, rad-9e2z]
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

**2026-07-14T04:59:00Z**

2026-07-13 Pixel evidence: the Markdown command was split across multiple transcripts, and one 34.8s endpoint contained only 0.55s of detected voice. Oxford-derived short-pause, noise, and echo/segmentation fixtures are now part of rad-9e2z and the conformance expansion.

**2026-07-14T05:26:25Z**

Added a configurable 30s maximum utterance boundary so intermittent noise/echo cannot recreate the observed 34.8s endpoint; unit coverage injects voiced spikes and proves bounded finalization. The seeded corpus now covers natural pauses, noise, echo, and frame loss, and the end-to-end LiveKit suite remains under the latency budget. Keep open for the remaining explicit long-pause/barge-in/echo ownership matrix.

**2026-07-14T12:02:23Z**

2026-07-14 automated live speech-barge matrix increment passed: the harness interrupted a currently audible long response with a replacement utterance, observed the old Sidecar stream close, stopped old audio in 208 ms, and returned only the replacement answer. Existing acoustic fixtures cover short natural pause, background noise, speaker echo, and frame loss. Remaining acceptance work is the explicit long-pause/echo ownership matrix, so this ticket stays open.

**2026-07-15T03:43:46Z**

2026-07-14 long-pause ownership increment passes locally: a 1.2s intra-request pause produced zero early agent-audio frames, the replacement clause had exactly one model owner, and first audio arrived in 2.038s. Endpoint tests also hold the first owner through the endpoint candidate before replacement. All automated short/long pause, noise, echo, frame-loss, tap, and speech-barge cases now pass. Remaining acceptance is explicit confirmation that the successful Pixel run used the physical speaker route (rather than headphones/Bluetooth), so the ticket remains open.
