---
id: rad-n62u
status: in_progress
deps: []
links: []
created: 2026-07-20T14:32:29Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, android, voice, playback, barge-in, regression]
---
# Restore future Android playback after interruption

After verified speech interrupts current model audio, later model responses remain silent on the physical Pixel even though the conversation continues.

## Design

Keep the immediate speaker mute and stale-frame rejection, but scope invalidation to the interrupted operation generation and deterministically re-arm playback for the next generation.

## Acceptance Criteria

Interruption stops current audible output promptly; late audio and playback announcements from the interrupted generation remain rejected; the immediately following and subsequent model turns are audible without reconnecting or toggling pause; playback acknowledgements retain the interrupted operation generation; automated Android/media-worker regression tests and a physical Pixel sequence pass.


## Notes

**2026-07-20T14:34:32Z**

Root cause confirmed from the physical-phone report: after SessionEngine.interrupt increments the session generation, LiveKitEventSink serialized the interrupted operation using the new session generation rather than operation.generation. Android invalidated that new generation, rejected the following turn's PlaybackSegment, and never reached its safe unmute path. The event sink now preserves the cancelled operation generation. A regression passes a generation-4 interrupted operation alongside generation-5 session state, and the existing Android tracker regression proves generation 4 remains rejected while generation 5 starts playback. Focused verification: 33 media-worker tests and DevicePlaybackTrackerTest pass. Keep open for the requested Pixel interrupt-then-follow-up acceptance run.

**2026-07-20T14:40:05Z**

External LiveKit report artifacts/voice-conformance/20260720T143800Z/report.json passes on deployed code. Speech barge-in stopped old-turn heard audio in 0.440s; the interrupted operation remained generation 4; replacement playback arrived on generation 5 and was heard after 2.290s; the abandoned HTTP stream closed. This is the automated end-to-end invariant that previously allowed Android to remain muted.
