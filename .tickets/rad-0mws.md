---
id: rad-0mws
status: in_progress
deps: []
links: [rad-fvx9, rad-9e2z, rad-rc9v]
created: 2026-07-17T23:53:05Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-ghti
tags: [radcliffe, stt, android, audio, quality, regression, operator-final-check]
---
# Stop losing and misdecoding the first live Pixel utterance

A newly opened Pixel conversation can omit the first one or two spoken sentences and materially misdecode the next sentence even though synthetic STT conformance is green.

## Design

Separate transport readiness, endpoint capture, and decoder accuracy. Do not expose Listening until the worker has subscribed to the participant audio track. Add an explicit opt-in owner-only capture mode for a small real-speaker Pixel corpus, then evaluate identical captured PCM across candidate local decoders and decoding settings. Never use an LLM to silently invent or rewrite uncertain user intent.

## Acceptance Criteria

The UI cannot report Listening before the worker confirms audio-track subscription and input processing; a first utterance begun immediately after that confirmation retains its leading phonemes and sentences; an opt-in test mode retains bounded 0600 WAV plus expected/actual transcript and deletes or excludes audio from ordinary logs; a corpus spoken by the target user through the Pixel covers first-turn speech, multi-sentence requests, short commands, natural pauses, room noise, and speaker/earbud routes; the chosen local pipeline reaches macro WER at most 0.03, exact required-keyword recall 1.0, and zero dropped leading words on that corpus; low-confidence or materially disagreeing hypotheses ask for clarification instead of silently changing intent; and the external suite distinguishes transport loss, endpoint truncation, and decoder error with retained timing evidence.


## Notes

**2026-07-18T03:09:03Z**

Implemented the non-consent-bound work on 2026-07-17: Android no longer reports Listening from its own connect/reconnect; the worker waits for a subscribed microphone track and its first processed frame, then emits state plus targeted SessionReady. Added owner-identity-scoped, explicit opt-in private capture with 24-case/30-second bounds, 0700 directory and 0600 plan/WAV/JSON, expected/actual transcript plus transport/endpoint/decoder timing, identical-PCM multi-profile benchmarking, the 0.03 macro-WER/1.0 keyword/zero-leading-drop gate, and transport-vs-endpoint-vs-decoder diagnostics. Optional independent local verification now asks a fixed clarification on low confidence or material disagreement without persisting a USER turn or invoking an LLM. make check passes (141 Python, 21 Sidecar, Android unit/lint); external LiveKit report artifacts/voice-conformance/20260718T030507Z/report.json passes all checks, including readiness 248.64 ms after microphone publication and stop/disconnect/track/rejoin. Keep open pending explicit owner opt-in and the target-speaker physical Pixel corpus across speaker and earbud routes, followed by measured profile selection.

**2026-07-18T04:18:32Z**

Completed physical Pixel validation on 2026-07-17 with explicit owner consent. Fixed the production managed-worker environment allowlist so owner-scoped capture and optional verification settings reach dynamically created Pixel rooms; added a regression test. Captured six accepted target-speaker cases spanning first turn, multi-sentence, short commands, a natural pause, room noise, and speaker/earbud routes. All WAV/JSON pairs are mode 0600 in a 0700 directory, hashes match, and durations are under 30 seconds. First input arrived 64.84 ms after track subscription with 600 ms pre-roll. Private benchmark report artifacts/private-stt-capture/rad-0mws/corpus/benchmarks/20260718T041748Z/report.json passed full coverage: large-v3-turbo, large-v3-turbo-domain, and large-v3 each achieved macro WER 0.0, keyword recall 1.0, and zero dropped leading words; all cases classify no_error_detected. Kept large-v3-turbo-domain because it already passes without verifier overhead. Capture was disabled immediately after the corpus. Final make check passes: 141 Python passed/1 skipped, 21 Sidecar tests, Android unit/lint, ruff, formatting, and mypy; git diff --check passes.

**2026-07-21T18:15:46Z**

Reopened 2026-07-21 after recurrence in ordinary non-capture mode. Session ses_0871ab859cecd63c47447826759d2c0e USER sequence 1 retained only Using your read source tool; the next turn explicitly reported that the intended preceding request was not captured. Later accepted turns of 4.3s, 5.3s, and 16.6s were complete, so investigate first-turn subscription/readiness, buffering, and endpoint state before decoder length limits.

**2026-07-21T18:18:39Z**

Root cause and implementation 2026-07-21: the worker was fully subscribed and input-ready before speech, but the affected first utterance reached the 30,025 ms maximum_utterance endpoint with 22,412.5 ms foreground voice and no trailing silence. The uninterrupted 2,681 ms tail then began while the first cold transcription task was active, was classified as a barge-in, and cancelled the first 30 seconds; only the tail reached USER sequence 1. Raised the default hard utterance bound to 120 seconds while retaining silence endpointing and the explicit configurable safety cap. Added a deterministic 32-second continuous-speech regression proving one transcription, one USER handoff, and zero false barge-ins. Full repository verification passes: 340 Python tests with 1 skipped, 98 sidecar tests, TypeScript checks, Android unit tests, and lint. Keep in progress for a fresh-session Pixel utterance longer than 30 seconds.
