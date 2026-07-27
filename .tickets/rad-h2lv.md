---
id: rad-h2lv
status: closed
deps: [rad-wnwh, rad-58mt, rad-8i08]
links: [rad-lod5, rad-mp26, rad-yk82]
created: 2026-07-13T01:37:25Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, android, milestone-1, operator-final-check]
---
# Build the Android VoiceSessionService connectivity foundation

Create the native Kotlin/Compose Android client needed to perform the Milestone 1 voice, interruption, provenance, and pause/resume demo on the target Pixel.

## Acceptance Criteria

The Android app uses Kotlin, Compose, and the LiveKit Android SDK; VoiceSessionService is a user-started foreground service declared for microphone and media playback; the app can start/end a room, publish microphone audio, play agent audio, display user/model/source messages distinctly, and show listening/thinking/speaking/reading/paused state; tap and speech barge-in flush audible output; Media3 controls pause, resume, and stop from the notification or a hardware media control; the demo passes on the target Pixel speaker; and no microphone capture begins without explicit user action.

## Notes

**2026-07-13T03:02:56Z**

Implementation is committed and Android test/lint pass: permission-gated foreground MediaSessionService, LiveKit microphone/audio, Protocol v2 controls/events, reconnect state, provenance UI, and notification actions. Remaining acceptance evidence is the target Pixel speaker, hardware/lock-screen, and speech-barge run.

**2026-07-13T04:10:14Z**

Fixed physical Pixel bootstrap failure found during demo: Build.MODEL included spaces (Pixel 8 Pro), violating the control-plane participant identity contract. Android now sanitizes identities to pixel-Pixel-8-Pro with bounded length and fallback coverage; debug APK bumped to 0.1.1 (versionCode 2).

**2026-07-13T04:37:37Z**

Protocol v2 control/data interoperability is now live: the Pipecat adapter preserves binary Protobuf instead of decoding UTF-8; pause suppresses microphone turns, resume restores listening, interrupt flushes/cancels and returns listening, and worker ServerEvents drive Android thinking/speaking/listening plus user/model messages. Live harness measured interrupt acknowledgement at 51ms. Physical Pixel confirmation remains.

**2026-07-14T02:46:33Z**

User physically verified the v0.1.1 Pixel app connects, displays state, plays responses, and the on-screen interrupt, pause/resume, and end controls basically work. Persistent setup fields, automatic sessions, recent-session navigation, lock-screen/hardware evidence, and context usage UI remain follow-up work.

**2026-07-17T23:27:52Z**

2026-07-17 physical follow-up: with the Pixel screen locked, the active voice session continued to hear the user and respond. Headset hardware controls remain deferred because no headset was available.

**2026-07-27T14:54:57Z**

Milestone 1 Android connectivity acceptance is complete from cumulative physical Pixel evidence. The app has repeatedly created/joined/ended rooms, published microphone audio only after explicit user action, rendered distinct user/model/source events and states, and played agent audio. Physical acceptance covers on-screen and speech interruption, speaker operation, active locked-screen conversation, and app/media-control source pause/resume without replay (session ses_d94bbaad38276fc8a0c5076cfc6e2e9f). Notification media control satisfies the ticket's notification-or-hardware criterion; broader Bluetooth/headset route coverage remains post-M1 rad-g9yr.
