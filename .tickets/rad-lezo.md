---
id: rad-lezo
status: closed
deps: [rad-au20, rad-ihso]
links: [rad-l1jc, rad-ncdz, rad-1w52]
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

**2026-07-17T00:44:30Z**

2026-07-16 Pixel v0.4.0 acceptance passed. The user confirmed completed sentences did not replay and left the result in the live conversation. SQLite corroborates the device path for ses_d94bbaad38276fc8a0c5076cfc6e2e9f: source boundaries 0 and 1 completed; boundary 2 flushed at 680 ms under playback attempt 7b571e5e-1021-4e37-a735-79edde8d71b4; boundary 2 resumed under fresh attempt 9e4731a9-6644-4948-9f36-9490a88b7481 and completed; boundary 3 then completed. This satisfies physical app/media-control resume acceptance.
