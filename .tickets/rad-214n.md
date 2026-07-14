---
id: rad-214n
status: open
deps: []
links: []
created: 2026-07-14T02:47:05Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, milestone-2, voice, cancellation, regression]
---
# Make voice-turn cancellation generation-safe

Fix the physical-run race where an interrupted unanswered prompt remains active in the next projection and overlapping endpointed turns can both persist model output or attempt state transitions.

## Design

Give each turn an immutable cancellation object and operation generation. Serialize ownership changes under one session-level coordinator. Persist an explicit interrupted or superseded disposition and make ContextProjection exclude superseded content by policy while retaining the audit record.

## Acceptance Criteria

A new transcript atomically cancels and awaits the previous active turn before it can begin; old tasks never read a replacement turn cancellation object; only the current generation may persist model output, publish audio, acknowledge playback, or transition state; interrupted user and partial-model envelopes remain auditable but are deterministically excluded or annotated according to the documented projection policy; the observed count-then-model-question sequence does not answer the abandoned count request; the observed listening-to-listening InvalidTransitionError is reproduced by a regression test and eliminated; tap and speech interruption still acknowledge within 500 ms; and race tests run repeatedly without duplicate replies or orphaned HTTP streams.

