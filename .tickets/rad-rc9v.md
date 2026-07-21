---
id: rad-rc9v
status: in_progress
deps: []
links: [rad-fvx9, rad-9e2z, rad-0mws]
created: 2026-07-21T18:19:39Z
type: feature
priority: 0
assignee: coopwilliams
tags: [radcliffe, stt, nvidia, parakeet, model-upgrade, voice, operator-final-check]
---
# Evaluate and switch Radcliffe STT to NVIDIA Parakeet TDT 0.6B v3

Repeated physical Pixel sessions show that the current local Whisper profile can materially misparse or omit spoken words even when transport and endpoint timing are healthy. Evaluate NVIDIA Parakeet TDT 0.6B v3 as the replacement production STT model and switch Radcliffe if it meets the measured quality, latency, privacy, and deployment gates.

## Design

Add a provider implementation that preserves the existing DetailedTranscriber evidence contract or explicitly maps equivalent Parakeet confidence and timing evidence. Benchmark identical retained owner-consented PCM against the current Whisper baseline, keep inference local unless separately authorized, make model loading/readiness observable without transcript logging, and retain a rollback selector until phone acceptance passes.

## Acceptance Criteria

On the existing owner-consented Pixel corpus plus new difficult natural-speech cases, Parakeet achieves lower WER than the current profile, exact required-keyword recall 1.0, and zero dropped leading or trailing words; first-turn warmup and steady-state latency stay within the voice budget; silence and playback do not create USER turns; cancellation, pause, rejoin, and long-utterance tests pass; a fresh APK/server deployment passes speaker and earbud Pixel verification; the old Whisper model remains an explicit rollback option but is no longer the user-test default.


## Notes

**2026-07-21T18:19:39Z**

Requested by the operator after the 2026-07-21 phone tests because word-level recognition still frequently whiffs independently of the separately root-caused 30-second endpoint split.

**2026-07-21T18:34:21Z**

Implementation and host benchmark 2026-07-21: added pinned parakeet-mlx 0.5.1 with mlx-community/parakeet-tdt-0.6b-v3, local Apple provider, serialized inference, 16 kHz mono input, sentence/token confidence mapping into DetailedTranscriber evidence, long-audio chunk controls, startup preload/warmup before input readiness, environment allowlisting, and explicit RADCLIFFE_SPEECH_PROVIDER=whisper rollback. local and parakeet now select Parakeet; protected user-test config explicitly selects parakeet. On an identical 35.928-second synthetic Gettysburg fixture, warmup took 1480.88 ms, steady transcription 581.5 ms (RTF 0.0162) versus Whisper 1074.82 ms (RTF 0.0299); both retained first and last content and had the same measured WER 0.03883, while Parakeet passed the confidence gate. This is host/runtime evidence only; keep open for target-speaker Pixel acceptance and diagnosis of the independent pause/cancellation bug.
