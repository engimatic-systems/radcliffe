---
id: rad-0mws
status: in_progress
deps: []
links: [rad-fvx9, rad-9e2z, rad-rc9v, rad-wbza]
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

**2026-07-21T18:34:21Z**

Latest phone evidence 2026-07-21: the 120-second cap fixed the original hard-boundary case but did not fix ordinary natural pauses. In ses_a4adbd5f31ad42e5aac83433270c09e0, 8.918 seconds of audio endpointed on 900 ms silence and continuation 571 ms later was classified as barge-in, cancelling the first still-transcribing turn; only the later 21.793-second chunk persisted. In ses_d0773c0ae235f602618a062d772b073d, a 26.656-second Gettysburg chunk endpointed on silence and continuation 422 ms later cancelled it; only the 2.750-second tail persisted. This isolates endpoint/turn-cancellation semantics from the model-quality question. Keep open through the Parakeet A/B; if the faster preloaded model still drops paused continuations, preserve completed user audio/transcription instead of cancelling it as model-output barge-in.

**2026-07-21T18:54:22Z**

Barge-in lifecycle fix 2026-07-21: endpoint capture/STT completion is no longer cancelled when verified speech stops an active model/playback turn. Completion tasks are tracked independently, processed in capture order, and hand accepted text to a separately owned model task only after the USER envelope is persisted and active. Speech beginning within a configurable 750 ms of an endpoint is marked continued rather than interrupted; continued user/model fragments remain in chronological context, while genuine later interruptions retain the existing excluded-turn behavior. If STT remains blocked until the entire following clause endpoints, the ordered verified handoff explicitly marks the prior turn continued before submitting the next transcript. Deterministic regressions reproduce the observed 422 ms and 571 ms phone resumptions, the slower-than-following-clause STT race, context inclusion of both Gettysburg fragments, and a later true interruption. Full make check passes: 349 Python tests with 1 skipped, 98 sidecar tests, TypeScript, Android unit/lint, ruff, formatting, and strict mypy. Keep open only for a future quiet-time physical phone acceptance; no APK change is required.

**2026-07-27T04:43:08Z**

2026-07-26 physical-phone natural-pause review did not satisfy the context-preservation portion of acceptance. Session ses_407155ef8e43d090e3a5737382aad8b9 retained all three spoken fragments accurately, but the first two turns were marked interrupted rather than continued; the final Sonnet turn replied that it had no prior context. Worker telemetry classified both post-endpoint resumptions as disposition=interrupted under the 750 ms continuation grace. Keep open: transport/STT retention is verified, but natural-pause fragments still fall out of model context.

**2026-07-27T04:57:01Z**

2026-07-26 follow-up phone session ses_1606ba8a43d29f5ba7463625bd551ba4 provides a second deterministic natural-pause failure. The complete first request was persisted at 04:50:29.868Z. The one-word addendum 'Verbatim.' began while generation 1 was active but before any model delta, synthesized audio, playback segment, tool call, or model envelope existed. Because speech resumed outside the 750 ms post-endpoint grace, production classified it as disposition=interrupted at 04:50:29.997Z. context_replay then excluded the first USER envelope, so generation 2 truly received only the addendum and denied having request context. This is not a swallowed-response problem. Mitigation should separate response cancellation from user-context retention: treat verified speech before any client-heard assistant output as semantic continuation regardless of endpoint grace, carry unanswered user fragments into the next effective userMessage, and retain accepted USER envelopes while persisting only client-heard assistant prefixes. Add exact phone-timing regressions for no model delta, generated-but-unheard output, partially heard output, and an actual replacement request.
