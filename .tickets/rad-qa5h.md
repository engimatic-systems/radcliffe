---
id: rad-qa5h
status: in_progress
deps: [rad-pb5a]
links: []
created: 2026-07-28T21:38:21Z
type: feature
priority: 0
assignee: coopwilliams
parent: rad-0tmq
tags: [radcliffe, android, voice, playback, backpressure, reconnect, audio]
---
# Make Android the bounded playout-capacity and physical-progress authority

Implement the Android half of the approved playback protocol. The phone must grant only capacity it can honor, correlate announced attempts with decoded PCM, and report physical start, progress, completion, flush, or failure without allowing delayed media or metadata to build an unbounded queue.

## Design

Keep one serialized receiver actor inside VoiceSessionService. It owns the local admission window, decoded-PCM cursor, route-specific playout lifecycle, and reconciliation snapshot. Grant bounded credit from measured local capacity, consume it exactly as specified, and never infer completion from receipt of metadata or elapsed wall time. Define deterministic handling for metadata-before-media, media-before-metadata, audio focus loss, Bluetooth or route change, pause, interrupt, track loss, process death, duplicate packets, and reconnect. Persist only the minimum non-secret state needed for safe recovery.

## Acceptance Criteria

Android never accepts more admitted-unheard audio than its advertised bound and never emits acknowledgements for unconsumed PCM. Each accepted attempt has one legal monotonic lifecycle and one terminal outcome; duplicates and stale epochs are harmless. Local interruption or route loss stops audible old-generation output within the existing latency gate and reports the modeled flush or failure. After network or process recovery, the reconnect snapshot converges with the server or explicitly discards uncertain audio rather than replaying a backlog. Coroutine and instrumented tests cover every receiver transition, and implementation traces refine the machine-checked model.

## Notes

**2026-07-29T01:07:50Z**

First receiver slice implemented: Android retains bounded latest physical acknowledgements, suspends uncertain local playback on reconnect, waits for room plus playback-track readiness, sends the snapshot/capacity handshake, and remains muted until replay admission or no-replay reconciliation. Route/process-death coverage remains open.

**2026-07-29T04:22:34Z**

Implementation is complete on epic/rad-0tmq-complete-protocol: Android owns W=1 acceptance/decoded progress, persists content-free snapshots synchronously across process death, rejects stale revision/epoch/credit/generation, stays muted until matching metadata acceptance, and reconciles transport plus speaker/Bluetooth route changes. Unit/lint are green. This remains open because no Pixel is attached to run the required instrumentation/route acceptance.
