---
id: rad-lezo
status: in_progress
deps: [rad-au20, rad-ihso]
links: [rad-l1jc, rad-ncdz]
created: 2026-07-15T03:44:49Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, playback, android, source]
---
# Persist client-heard source playback boundaries and resume exactly

Make the device-authoritative playback ledger real for canonical source reading so pause, reconnect, and resume continue from a persisted deterministic boundary rather than replaying or discarding the source.

## Acceptance Criteria

The worker publishes source audio in stable sentence boundaries; Android sends reliable started/position/completed/flushed/failed acknowledgements tied to the current operation and segment; the server validates and persists only current acknowledgements; pause flushes audible output without converting it to an interrupt disposition; resume continues at the first unacknowledged source boundary without another model/tool call; duplicate and stale acknowledgements are idempotent or rejected; fake and external LiveKit tests prove no duplicate/skipped canonical segment; and the Pixel can pause/resume a supplied Markdown source from both app and media controls.


## Notes

**2026-07-15T03:54:21Z**

2026-07-14 first resumable-source increment is green locally. Source segments now remain active for their PCM duration; Pause flushes the unfinished sentence without interrupting the conversational turn; Resume reads SQLite and replays from the last completed boundary without another Pi/tool call. External LiveKit evidence: 3ms pause acknowledgement, 67ms audible stop, 535ms resumed source audio, one TEXT_SOURCE envelope, one flushed boundary, first segment restarted exactly once, final boundary 4. Report: artifacts/voice-conformance/20260715T035142Z/report.json. The ticket remains open for device-authored Android audio-sink acknowledgements and physical Pixel verification.

**2026-07-16T22:15:23Z**

2026-07-16 device-authoritative increment is locally green. Protocol v2 announces immutable playback segments with a unique attempt ID; production Android reports started/250ms position/completed from the decoded LiveKit PCM sink and flushes before pause, interrupt, or stop; the worker validates operation, segment, boundary, monotonic position, duration, and transition order before persistence. External LiveKit evidence passed 35/35 checks, including distinct interrupted/resumed attempt IDs and exact client-to-SQLite provenance: artifacts/voice-conformance/20260716T221109Z/report.json. Full repository checks passed (90 Python, 18 Node, Android unit/lint) and session conformance passed 24/24. APK checkpoint: Radcliffe-device-playback-v0.4.0-debug.apk. Ticket remains in progress solely for Pixel app and notification/media-control pause/resume acceptance.
