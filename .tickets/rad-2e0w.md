---
id: rad-2e0w
status: closed
deps: []
links: []
created: 2026-07-21T04:04:49Z
type: bug
priority: 2
assignee: coopwilliams
parent: rad-cnrg
tags: [voice, spoken-output, interruptions, operator-autonomous]
---
# Preserve visual detail in interrupted split-channel turns

Completed turns retain raw model text in the timeline while speaking a bounded alternative, but interrupted partial turns still key durability to completed spoken bytes. Add explicit segment-boundary mapping so partial persistence retains the corresponding raw display text without claiming unseen model output.

## Design

Carry display/spoken segment pairs through playback boundaries and persist only display segments whose spoken alternatives reached the existing durable playback boundary. Keep source rendering and exact-output bypass unchanged.

## Acceptance Criteria

Interrupted and failed turns persist the raw display counterpart of completed spoken segments; no unplayed display detail is persisted; playback retry and barge-in tests cover substitutions longer and shorter than their display text; telemetry remains content-free.

## Notes

**2026-07-23T03:19:14Z**

Implemented playback-aware display/spoken segment pairing. Each spoken chunk records the raw display text completed by its final playback segment; interrupted and failed turns rebuild only the contiguous display prefix whose playback acknowledgements crossed their durable boundary. Completed client-authoritative turns defer model persistence until all playback completes, and queued/flushed detail is never added later. Regressions cover longer and shorter alternatives, blocking barge-in, failed playback followed by retry, zero-ack queued audio, and Android-authoritative first-completed/second-flushed boundaries; telemetry assertions remain content-free. Full make check passes: 376 Python passed/1 skipped, 107 sidecar tests, TypeScript, Android unit/lint, Ruff, formatting, and mypy.
