---
id: rad-jypn
status: open
deps: []
links: [rad-16wv]
created: 2026-07-21T02:40:24Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-3se2
tags: [voice, interruption, vad, silero, p0, operator-autonomous]
---
# Handle near-field barge-in when Silero reports zero speech windows

A genuine overlapping phone utterance can be retained as unconfirmed_barge_in when the Silero verifier reports zero speech windows despite strong near-field energy. This is distinct from queued playback ownership: the worker may correctly retain foreground ownership yet still decline to interrupt because the speech-confidence branch is indeterminate.

## Design

Preserve echo, room-tone, and silence rejection while making the high-energy/zero-window branch observable and robust. Correlate candidate, acoustic evidence, verifier disposition, turn, generation, flush request, and device acknowledgements. Use retained evidence from session ses_5fb0ac5ab311fbda54bb9652cb9b68b0 to build a deterministic reproduction before changing thresholds or fallback policy.

## Acceptance Criteria

1. The retained high-energy/zero-window candidates from ses_5fb0ac5ab311fbda54bb9652cb9b68b0 have a deterministic regression. 2. Genuine near-field overlap reaches a confirmed interruption or an explicit recoverable disposition instead of silently becoming a queued normal turn. 3. Echo, room tone, background babble, and silence retain their current false-interruption protections. 4. Telemetry correlates candidate, acoustic features, verifier result, turn, generation, flush request, and device acknowledgement. 5. External voice conformance covers both accepted near-field speech and rejected non-speech.
