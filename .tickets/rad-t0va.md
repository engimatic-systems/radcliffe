---
id: rad-t0va
status: in_progress
deps: []
links: [rad-20nk, rad-16wv]
created: 2026-07-20T14:32:29Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-1wji
tags: [radcliffe, android, ui, configuration, phone-review]
---
# Simplify Android behavior controls after phone review

The compact behavior buttons still include the words Pace and Detail, and a visible Undo behavior change button appears after a mutation.

## Design

Render only the compact effective values on the two buttons while retaining descriptive TalkBack semantics. Remove the visible Undo control without removing the server-side or spoken undo capability.

## Acceptance Criteria

The behavior buttons show only the registered effective value or compact detail symbol; the visible words Pace and Detail are absent; TalkBack still identifies both settings and their current/next values; no Undo button appears after a setting change; deterministic Android tests pass.


## Notes

**2026-07-20T14:36:01Z**

Implemented from the 2026-07-20 phone review. Visible behavior buttons now render only the current compact value (0.75x/1.0x/1.25x/1.5x and +/++/+++); descriptive Pace/Detail names and next-value guidance remain only in TalkBack semantics. Removed the post-mutation Undo behavior change button without removing server-side or spoken undo support. Android unit tests and lint pass. Keep open for the bundled Pixel visual/TalkBack confirmation.
