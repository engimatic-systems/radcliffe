---
id: rad-fnho
status: closed
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

## Notes

**2026-07-28T21:56:44Z**

Started on the dedicated rad-0tmq epic branch. First deliverable is the content-free end-to-end buffer/authority map and reproducible measurement plan; this spike will not claim a production flow-control fix.

**2026-07-29T00:45:07Z**

Physical Pixel baseline completed across three sessions. The operator reports deterministic-source interruption (A) and ordinary model interruption (B) passed. Phase C failed semantic continuity: after Tailscale was disabled after audible number 4, playback resumed at number 7, silently omitting 5 and 6. Correlated Phase C evidence is physical session ses_2648584333f9dd384f3e2c6eb3e37eb6 / logical session ses_0a1821d30918a640522c / operation a55525e8-fb65-4347-be57-9d7b71a6ab6e. Android durably completed playback attempts model:0 through model:6 and started model:7; its last acknowledgement was at 2026-07-29T00:28:34.236Z. The worker continued synthesizing and publishing additional segments until 00:28:42.655Z. LiveKit reported the participant disconnect/reconnect only at 00:28:49.446Z/00:28:49.447Z. Thus sender admission continued for more than eight seconds after receiver evidence ceased, and reconnect resumed the current realtime media position without restoring the missing semantic sequence. Current Android deduplication is an in-memory set that rejects a duplicate playback_id without replaying its prior terminal acknowledgement; the worker retains pending attempts but has no receiver-credit admission gate or reconnect snapshot/reissue path. Design consequence: stop admitting when granted receiver credit is exhausted; retain stable ordered segment content; reconcile terminal/partial/unseen attempt state after reconnect; idempotently re-ack completed attempts; replay or safely resume only uncompleted attempts in order; and let cancellation dominate any replay.

**2026-07-29T01:07:50Z**

The Phase C trace now has an enforceable seam documented in docs/bounded-playback-reconnect.md: sender admission immediately before Protocol v2 playback announcement/media publication; receiver credit returned only by decoded-PCM terminal evidence. The temporary-disconnect slice uses W=1 and D=30s. Remaining spike scenarios and physical measurements are still open.

**2026-07-29T04:22:34Z**

Completed on epic/rad-0tmq-complete-protocol. docs/bounded-playback-protocol.md maps every buffer/owner/bound/observable/control seam, incorporates the prior Pixel traces and gap measurements, specifies the exact durable-announcement/ACCEPTED admission seam, and provides reproducible content-free model/conformance commands. Harness result: 11 schedules, maximum admitted depth 1, maximum admitted duration 2,000 ms.
