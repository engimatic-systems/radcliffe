---
id: rad-fnho
status: open
deps: []
links: []
created: 2026-07-28T21:37:26Z
type: task
priority: 0
assignee: coopwilliams
parent: rad-0tmq
tags: [radcliffe, voice, playback, android, livekit, observability, spike]
---
# Map every audio buffer and choose the enforceable flow-control seam

Before choosing a credit unit or queue bound, measure where audio waits between synthesis and decoded Android PCM. The current worker publish call, Pipecat frame pipeline, LiveKit outbound track, network and jitter buffering, Android track subscription, and app playback-attempt queue do not expose one shared notion of queued duration. A protocol based on the wrong seam will still stack audio or will create avoidable gaps.

## Design

Build a content-free diagnostic sequence with stable operation, attempt, and segment IDs and distinct known durations. Exercise immediate delivery, slow synthesis, delayed Android consumption, induced data delay, media delay, disconnect/reconnect, background/foreground, and cancellation. Record enqueue, transport publication, Android acceptance, decoded-PCM start/position/terminal, and flush timing without recording conversation text or microphone audio. Determine which queues are observable and controllable, whether LiveKit audio and reliable metadata can arrive in different orders, the maximum useful segment size, and whether credit should be measured in attempts, PCM milliseconds, frames, or a composite bound.

## Acceptance Criteria

A checked-in report diagrams every buffering location, names its owner, states whether it is bounded and observable, and includes reproducible measurements from the local acceptance client and Pixel. The report identifies the exact sender admission point and receiver credit signal that can enforce a finite unheard-audio bound; documents metadata/media ordering behavior and reconnect behavior; recommends a credit unit plus segment-size rule; and lists any LiveKit or Pipecat limitation the formal protocol must model. The spike does not claim a production fix.
