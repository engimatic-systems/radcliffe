---
id: rad-h2lv
status: open
deps: [rad-wnwh, rad-58mt, rad-8i08]
links: []
created: 2026-07-13T01:37:25Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, android]
---
# Build the Android VoiceSessionService connectivity foundation

Create the native Kotlin/Compose Android shell that owns a LiveKit room, publishes microphone audio, receives agent audio, and survives expected mobile lifecycle events.

## Acceptance Criteria

The Android app uses Kotlin, Compose, and the LiveKit Android SDK; VoiceSessionService is a user-started foreground service declared for microphone and media playback; Media3 controls support pause, resume, and stop from notification and headset controls; app state reconnects after Wi-Fi/cellular change; speaker, wired headset, and Bluetooth routes are verified on the target Pixel; no background microphone capture begins without explicit user action.
