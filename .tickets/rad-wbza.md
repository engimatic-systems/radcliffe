---
id: rad-wbza
status: in_progress
deps: []
links: [rad-0mws]
created: 2026-07-27T05:02:45Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-ghti
tags: [radcliffe, voice, context, interruption, android, regression]
---
# Never erase accepted user speech when a turn is interrupted

Turn disposition currently applies to every envelope associated with a cancelled response. As a result, verified and persisted USER speech can be removed from model-visible history when later speech interrupts an active turn. Response cancellation must never erase words the user actually said.

## Design

Make context projection source-aware: every validated and persisted USER envelope remains in chronological model context regardless of interrupted, superseded, or failed response disposition, while disposition audit metadata remains durable. Response and playback cancellation continue independently. Preserve only the assistant output that the client actually acknowledged as heard. Ensure a follow-up or addendum can use the retained user context, including deterministic job-intent routing, without reactivating unheard assistant output.

## Acceptance Criteria

A deterministic reproduction of the phone trace persists a background-job request, begins a response turn with no model delta or playback, receives the later addendum 'Verbatim.' outside the endpoint continuation grace, and proves the next model/tool request sees both user utterances and launches one native run_job with the verbatim requirement. Persisted USER envelopes remain model-visible for interrupted, superseded, and failed response dispositions at any conversation position. Unheard assistant output remains excluded, acknowledged assistant prefixes retain their existing delivery semantics, a genuine later replacement remains the controlling instruction, replay remains deterministic, and focused domain/media/sidecar tests plus repository checks pass.


## Notes

**2026-07-27T05:10:15Z**

Implemented on 2026-07-26 in the stacked interruption-context branch. context_replay now applies interrupted/superseded/failed exclusion only to non-USER envelopes, retaining disposition annotations on accepted user speech. VoicePipeline carries an unanswered user instruction across an early barge-in with no model delta or tool start, so the next addendum reaches both model context and deterministic job-intent routing as one effective userMessage. Regression coverage reproduces the Gettysburg-plus-Verbatim trace, covers all excluded response dispositions, preserves genuine replacement chronology while excluding abandoned model output, and updates context accounting. Full make check passes: 462 Python tests with 1 skipped, 111 sidecar tests, strict ruff/mypy/TypeScript, Android unit tests, and lint. Keep in progress pending PR review and physical-phone acceptance.

**2026-07-27T13:57:00Z**

2026-07-27 phone retest exposed a follow-on duplication bug: the durable replay correctly held four USER envelopes exactly once, but the sidecar replaced the final packet with the cumulative unanswered userMessage, yielding A, B, C, then A+B+C+D to Sonnet. Fixed by keeping persisted packets unchanged in model-visible/context-meter messages while using the cumulative userMessage only for deterministic job, delivery, exact-output, and tool routing. Added regression coverage asserting four buffered fragments appear once each in chronological order and the Gettysburg-plus-Verbatim job batch still forces one native run_job. Validation: all 112 sidecar tests and TypeScript checks pass; focused media batching/interruption tests pass. Keep open for physical-phone acceptance of the corrected request shape.
