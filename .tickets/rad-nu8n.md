---
id: rad-nu8n
status: open
deps: []
links: [rad-rwfl, rad-lo0t, rad-chaa, rad-214n, rad-ghti]
created: 2026-07-14T04:05:29Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-3se2
tags: [voice, tts, reliability]
---
# Make local TTS option-safe and recover failed voice turns

The macOS say provider currently passes canonical text as a positional CLI argument. Text beginning with a dash is parsed as an option, which failed a live Pixel turn on - **Cosmic touch:** and left the voice engine unable to accept the next transcript.

## Design

Feed canonical UTF-8 through an option-safe input channel such as say -f. Treat synthesis or playback failures as a failed turn, flush pending audio, preserve the audit trail, and return the session to LISTENING without requiring a reconnect.

## Acceptance Criteria

Leading dash, double-dash, Markdown bullets, emoji, and multiline text synthesize without CLI option parsing; the live Cosmic touch regression is covered; a TTS failure records exactly one failed disposition and no orphaned pending audio; the session returns to LISTENING and accepts the next transcript; the real macOS say path and a deterministic failing fake are tested.

