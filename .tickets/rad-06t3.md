---
id: rad-06t3
status: closed
deps: []
links: []
created: 2026-07-17T02:36:40Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, voice, reconnect, android]
---
# Re-arm a persistent voice worker when a device rejoins

A device can end or lose its LiveKit connection while the managed worker remains alive. Reopening the durable conversation publishes and transcribes microphone audio, but the worker rejects the turn because its engine remains idle.

## Acceptance Criteria

After stop/disconnect and a fresh participant join, the existing worker resets endpoint state, advances to a fresh generation, returns to listening, accepts the next microphone turn, and produces one response without replaying abandoned work; unit and external LiveKit regression tests reproduce the exact lifecycle.


## Notes

**2026-07-17T02:44:32Z**

Root cause was captured from Pixel session ses_e03adde90aedff8a03b26eb2210c46fb: audio subscribed and Whisper produced 'Testing, testing, can you hear me?', then VoicePipeline raised InvalidTransitionError because KIND_STOP had left the persistent engine idle. The worker now re-arms an idle/failed engine and endpoint on participant join. Unit regression plus external LiveKit stop/disconnect/fresh-credential/rejoin passed with exactly one 'blue' response in artifacts/voice-conformance/20260717T024055Z/report.json.
