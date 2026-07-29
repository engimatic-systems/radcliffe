---
id: rad-l1ro
status: open
deps: []
links: [rad-grf7, rad-qa5h]
created: 2026-07-29T17:47:54Z
type: feature
priority: 2
assignee: coopwilliams
parent: rad-g9yr
tags: [radcliffe, android, bluetooth, headset, media-controls, mute, pause, post-m1]
---
# Map headset taps to microphone mute and conversation pause

Give Bluetooth/headset media controls explicit conversation semantics. The current on-screen play/pause control and one earbud tap toggle user microphone mute/unmute. Two earbud taps toggle pause/unpause for the conversation itself. Speaker-to-Bluetooth and Bluetooth-to-speaker route changes currently push the app into a listening-like state and must not be interpreted as control gestures.

## Design

Model microphone mute and conversation pause as separate explicit states. Remap the current on-screen play/pause affordance to an explicit microphone mute control. That control and a single headset media-button tap toggle microphone publication/input admission without toggling conversation pause. A double tap toggles the whole conversation between paused and active while preserving safe playback/session reconciliation. Disambiguate the gesture sequence deterministically, surface both states clearly, and treat audio-route/focus changes as state-preserving transport events rather than synthetic taps.

## Acceptance Criteria

One earbud tap and the remapped on-screen control reliably toggle microphone mute/unmute with visible state and no unintended pause. Two earbud taps reliably pause/unpause the entire conversation without producing a user turn or losing the resumable playback position. Speaker/Bluetooth transitions preserve mute and pause state, never enter listening solely because of the route change, and do not emit phantom gestures. Android unit/instrumented coverage and a locked-screen physical Pixel run cover one tap, two taps, route changes, interruption, and reconnect.
