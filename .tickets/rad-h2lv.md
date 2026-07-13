---
id: rad-h2lv
status: in_progress
deps: [rad-wnwh, rad-58mt, rad-8i08]
links: []
created: 2026-07-13T01:37:25Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, android, milestone-1]
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
