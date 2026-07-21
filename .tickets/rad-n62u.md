---
id: rad-n62u
status: closed
deps: []
links: [rad-16wv]
created: 2026-07-20T14:32:29Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, android, voice, playback, barge-in, regression, operator-final-check]
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

**2026-07-21T04:22:56Z**

Consolidated tip 7169f15 automated voice acceptance passed: artifacts/voice-conformance/20260721T042015Z/report.json. Foreground ownership survived a closed model stream with queued playback; background babble preserved 99 active assistant frames; speech barge-in acknowledged in 0.366s; old audio stopped in 0.419s; interruption advanced generation 4 to replacement generation 5; replacement audio arrived in 2.368s; and the later post-rejoin model turn was audible. APK staged for the operator-final check at /Users/ravd/agent-share/radcliffe/radcliffe-0.6.0-v10-7169f15-debug.apk (SHA-256 b9f0e7035f134d90d4ba4d33d629a4bd555b25b5fa8254c9c09901e1f9819320). Morning script: (1) install and connect; (2) say 'Count slowly from 1 to 60' and confirm all numbers are spoken with no tool-failure message; (3) ask for a five-sentence lighthouse story; (4) while it is audible, say 'Stop and reply with exactly pineapple'; (5) confirm old audio stops and pineapple is audible; (6) immediately ask 'What color is a clear daytime sky? Reply with one word' and confirm that answer is audible without reconnecting or toggling pause. Keep this ticket in progress until the physical Pixel result.

**2026-07-21T14:12:34Z**

2026-07-21 physical Pixel acceptance passed. In session ses_e2bc076a0ba13adc153e659d12741bf6, the lighthouse response was interrupted, the replacement generation answered 'Pineapple.', and multiple later model turns remained audible without reconnecting or toggling pause. This satisfies the remaining physical interruption/future-playback checkpoint. A separate download/read-source failure was observed afterward and is not a playback regression.
