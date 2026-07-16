---
id: rad-l1jc
status: in_progress
deps: [rad-wnwh, rad-8i08, rad-h2lv, rad-ihso, rad-g7di, rad-au20, rad-ghti]
links: [rad-enbx, rad-rwfl, rad-ghti, rad-9e2z, rad-ncdz, rad-lezo]
created: 2026-07-13T01:37:26Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, testing, milestone-1]
---
# Establish the Milestone 1 audio conformance suite and demo report

Port Oxford's audio-first acceptance philosophy into a focused Protocol v2 suite that verifies the Milestone 1 Pixel demo and produces reproducible evidence.

## Acceptance Criteria

The suite runs separately from the system under test or with isolated compute capacity; a reference implementation validates the harness; tests judge heard audio and cross-check state; the required scenarios cover a normal answer within five seconds, audible stop within 500 ms, pause/resume from a persisted boundary, a read_source CALL lifecycle, and exact canonical source text submitted to TTS; reports retain seeds, audio, envelope/state timelines, tool audit, and measured checks; and a documented Pixel demo procedure produces the same report bundle.

## Notes

**2026-07-13T03:02:56Z**

Credential-free contract report is implemented and passing with exact source/TTS hashes. Ticket remains open because the acceptance authority requires captured Pixel audio, isolated latency, TURN, interruption, and pause/resume evidence.

**2026-07-13T04:37:37Z**

Added scripts/livekit-voice-smoke.py as the first external live-room harness toward the Oxford-style suite. It publishes generated microphone audio through LiveKit, checks pause suppression, captures returned TTS audio, validates binary state/message events, enforces <5s first audio and <500ms interrupt acknowledgement. Current local run: 2.812s first audio, 0.051s interrupt acknowledgement.

**2026-07-14T02:46:33Z**

Physical exploratory run confirms the basic Pixel voice/control loop. It also supplied two valuable regression scenarios for the external suite: interrupted unanswered prompts must not bleed into the next projection, and a mid-utterance endpoint split must not create overlapping model completions or invalid state transitions.

**2026-07-14T04:59:00Z**

2026-07-13 tester feedback narrows manual acceptance to frontend-only behavior. Expand the local harness using Oxford audio-first patterns: seeded synthetic microphone audio, heard-output capture, state/envelope cross-checks, transcripts, latency, and retained artifacts. STT corpus and model selection are tracked by rad-9e2z.

**2026-07-14T05:26:25Z**

Oxford-derived black-box harness now creates an isolated LiveKit session and temporary worker store, paces seeded microphone WAVs in real time, captures heard agent audio into a timeline WAV, cross-checks Protocol v2 state/envelopes, and retains inputs/log/report. Local provider run passed pause suppression, exact normal and Cosmic-command STT, five consecutive <5s responses (2.144-2.680s), model content, 9ms interrupt acknowledgement, and 44ms audible stop. Evidence: artifacts/voice-conformance/20260714T052441Z/report.json. Routine backend/audio verification is now local; phone review remains for Android/WebRTC/UI behavior.

**2026-07-14T12:02:23Z**

2026-07-14 conformance hardening added real speech replacement and provider cold-readiness. After explicitly unloading qwen3:8b and restarting the Sidecar, health was withheld until the model was resident; the complete local run then passed first audio at 2.651 s, tap audible stop at 57 ms, and speech-barge audible stop at 208 ms. Report: artifacts/voice-conformance/20260714T120038Z/report.json.

**2026-07-15T03:43:46Z**

2026-07-14 the external LiveKit harness now exercises the real Pi read_source loop across an isolated worker database. The control plane transports the authorized protobuf source envelope through structured non-content tool details; the worker validates session/ID/kind/hash/provenance before local persistence and exact rendering. The passing report proves the 252-byte fixture and both SHA-256 fields, READING state, 2.857s source audio, five sub-5s responses, 8ms tap acknowledgement, and 206ms speech-barge acknowledgement: artifacts/voice-conformance/20260715T034110Z/report.json. The run also caught and fixed stale capability generations after interruption.

**2026-07-15T03:54:21Z**

2026-07-14 resumable source playback now passes the external suite: mid-sentence pause acknowledged in 3ms, audible source stopped in 67ms, resume returned to READING/audio in 535ms, SQLite recorded flushed boundary 0 then completed boundaries 1-4, and neither Pi nor read_source was invoked again. Report: artifacts/voice-conformance/20260715T035142Z/report.json. Physical Pixel source-card and app/media-control verification remains.

**2026-07-16T20:30:03Z**

2026-07-16 added a generated static voice review dashboard. make build-voice-dashboard indexes latest full LiveKit, false-turn, and STT suites, hard-links playable artifacts, exposes per-check pass/fail evidence and history, and publishes a 98.72s stereo review stream (microphone left, assistant right) at ~/agent-share/radcliffe/voice-tests/index.html. Latest dashboard contains 55 green checks across three suites.

**2026-07-16T21:45:39Z**

2026-07-16 latest local acceptance evidence: production STT corpus passes 13/13 at macro WER 0.0 (artifacts/stt-conformance/20260716T214311Z/report.json); endpoint rejection corpus passes 14/14 including quiet-onset 'OK', babble, echo, silence, and short commands (artifacts/stt-rejection/20260716T214212Z/report.json); full LiveKit/Whisper/Ollama/TTS run passes 34/34 including babble preservation, speech barge-in, abandoned-turn exclusion, exact source bytes, persisted source resume, and no model emoji (artifacts/voice-conformance/20260716T214321Z/report.json). Dashboard republished under ~/agent-share/radcliffe/voice-tests/.
