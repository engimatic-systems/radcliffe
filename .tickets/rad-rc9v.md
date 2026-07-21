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

**2026-07-21T18:39:30Z**

Deployment correction 2026-07-21: the first generated-session dispatch smoke exposed that per-session workers intentionally inherit a restricted PATH, while parakeet-mlx transcribe(path) redundantly shells out to ffmpeg. The compatibility worker passed only because its launcher shell had Homebrew on PATH. Changed the provider to feed Radcliffe already-normalized 16 kHz mono samples directly through Parakeet get_logmel/generate, preserving the upstream chunk overlap/stitch algorithm for audio over 120 seconds and eliminating the subprocess/PATH failure. A fresh 20.928-second runtime fixture passed with 1052.43 ms warmup, 424.11 ms inference (RTF 0.0203), retained first and last content, and passed the evidence gate. Repeat dynamic dispatch after redeploy remains required.

**2026-07-21T18:40:55Z**

Dynamic deployment acceptance 2026-07-21: after the in-memory correction, make check-worker-dispatch-local created ses_810b32bef22f3053f6d1117bafaca1e0. Its restricted-environment worker preloaded Parakeet in 1646.81 ms, connected to LiveKit only after preload, reached pipeline ready, and logged no ffmpeg, permission, traceback, or provider error. Control /healthz and /readyz were green on revision 4676f2a with Haiku 4.5. Keep this operator-final-check ticket in progress for the requested target-speaker Pixel Gettysburg/natural-pause A/B.
