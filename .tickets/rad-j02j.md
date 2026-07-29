---
id: rad-j02j
status: open
deps: []
links: [rad-grf7, rad-0tmq]
created: 2026-07-29T17:47:54Z
type: bug
priority: 2
assignee: coopwilliams
tags: [radcliffe, voice, playback, restart, recovery, post-m2]
---
# Preserve unheard sentence tails across worker restarts

Physical bounded-playback test 6 restarted the worker/backend during an audible sentence. Reconnection succeeded, but the latter unheard portion of the interrupted sentence was silently skipped. Preserve semantic continuity across worker and full-backend restart without reopening the completed protocol PR.

## Design

Recover the retained playback attempt from durable decoded-progress evidence rather than treating transport publication or pre-restart playback as completion. Reconcile the uncertain current part before admitting later speech: resume from confirmed progress where practical, otherwise replay the uncertain part at most once. Cover deterministic-source parts and ordinary model speech, and retain content-free operation/attempt/progress traces.

## Acceptance Criteria

Restart during the first, middle, and final portion of an audible sentence never silently omits its unacknowledged suffix and never advances to a later item first. The uncertain current part resumes or repeats at most once, cancellation still dominates recovery, no backlog bursts, and deterministic restart tests plus a physical Pixel run cover worker-only and full-backend restart.
