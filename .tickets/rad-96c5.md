---
id: rad-96c5
status: closed
deps: []
links: []
created: 2026-07-19T03:01:42Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-nhai
tags: [radcliffe, agent, jobs, voice, events]
---
# Schedule background job notifications at safe foreground pauses

Deliver durable, metadata-only background job lifecycle events to the active conversation while preserving foreground voice ownership.

## Design

Add a session-ordered job event feed and per-event delivery disposition. A media-worker scheduler emits typed JobEvent visibility immediately, maps only allowlisted lifecycle codes to fixed announcement phrases, and requests speech only through the existing foreground VoicePipeline owner when the endpoint is quiet and no assistant turn or stale playback is active.

## Acceptance Criteria

Job events have a durable session delivery sequence; visibility and speech dispositions survive worker restart; immediate, next-safe-pause, silent, and user-waiting policies are deterministic; no policy speaks over detected/candidate user speech, an active assistant turn, paused/stale audio, or a newer foreground submission; raw instructions, follow-ups, outputs, and transcripts never enter conversation events; reconnect and simultaneous-job tests prove ordered, idempotent delivery.


## Notes

**2026-07-19T03:22:17Z**

Implemented and verified on feat/safe-background-job-notifications. Added migration 010 with global delivery ordering and durable speech dispositions; Protocol v2 delivery_sequence; typed LiveKit replay; fixed allowlisted announcements; conservative endpoint, foreground-owner, paused-source, and current-playback gates; at-most-once restart behavior; and deterministic quiet/busy, race, silent, reconnect, simultaneous-job, barge-in, and shutdown coverage. make check: 276 Python passed, 1 skipped; 41 sidecar tests passed; Android unit tests and lint passed.
