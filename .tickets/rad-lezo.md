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
