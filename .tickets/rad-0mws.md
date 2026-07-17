---
id: rad-0mws
status: open
deps: []
links: [rad-fvx9, rad-9e2z]
created: 2026-07-17T23:53:05Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-ghti
tags: [radcliffe, stt, android, audio, quality, regression]
---
# Stop losing and misdecoding the first live Pixel utterance

A newly opened Pixel conversation can omit the first one or two spoken sentences and materially misdecode the next sentence even though synthetic STT conformance is green.

## Design

Separate transport readiness, endpoint capture, and decoder accuracy. Do not expose Listening until the worker has subscribed to the participant audio track. Add an explicit opt-in owner-only capture mode for a small real-speaker Pixel corpus, then evaluate identical captured PCM across candidate local decoders and decoding settings. Never use an LLM to silently invent or rewrite uncertain user intent.

## Acceptance Criteria

The UI cannot report Listening before the worker confirms audio-track subscription and input processing; a first utterance begun immediately after that confirmation retains its leading phonemes and sentences; an opt-in test mode retains bounded 0600 WAV plus expected/actual transcript and deletes or excludes audio from ordinary logs; a corpus spoken by the target user through the Pixel covers first-turn speech, multi-sentence requests, short commands, natural pauses, room noise, and speaker/earbud routes; the chosen local pipeline reaches macro WER at most 0.03, exact required-keyword recall 1.0, and zero dropped leading words on that corpus; low-confidence or materially disagreeing hypotheses ask for clarification instead of silently changing intent; and the external suite distinguishes transport loss, endpoint truncation, and decoder error with retained timing evidence.

