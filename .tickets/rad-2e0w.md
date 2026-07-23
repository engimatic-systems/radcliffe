---
id: rad-2e0w
status: open
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
