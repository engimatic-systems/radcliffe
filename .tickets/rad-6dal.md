---
id: rad-6dal
status: open
deps: [rad-214n, rad-fvx9]
links: [rad-l1jc, rad-fvx9, rad-214n, rad-ghti]
created: 2026-07-20T03:00:01Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, android, voice, barge-in, playback, regression]
---
# Stop audible playback when verified speech barge-in begins

The physical app detects that the user is speaking and begins interruption handling, but previously queued model audio continues playing over the user. Detection, server cancellation, and actual device-side audible cessation are not currently one atomic user-visible outcome.

## Design

Trace the verified foreground-speech signal through Android playback authority, LiveKit publication, the playback ledger, and VoicePipeline cancellation. As soon as speech is verified as a real interruption, locally flush or mute every current-generation playback segment and reject late frames while propagating cancellation upstream. Candidate noise must not stop playback before the existing near-field/Silero verification threshold.

## Acceptance Criteria

A deterministic regression reproduces verified user speech while multiple model-audio frames are queued; verification immediately invalidates and flushes current-generation audio, late/stale frames cannot restart it, and the model HTTP/TTS turn is cancelled without suppressing the replacement utterance. False candidate speech and speaker echo do not interrupt. Heard-output conformance measures verified-speech-to-silence under 500 ms, and a physical Pixel speaker test confirms the assistant no longer talks over a deliberate interruption.

