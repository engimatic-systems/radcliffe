---
id: rad-grf7
status: open
deps: [rad-g8os, rad-qa5h]
links: [rad-f4j2]
created: 2026-07-28T21:38:43Z
type: task
priority: 0
assignee: coopwilliams
parent: rad-0tmq
tags: [radcliffe, voice, playback, chaos, conformance, android, operator-final-check]
---
# Validate bounded playback under delay, partitions, retries, and restarts

Demonstrate that the implemented protocol preserves its proved properties at the real worker, LiveKit, and Android boundaries. This closes the gap between the abstract model and queues, callbacks, audio routes, and reconnect behavior in production adapters.

## Design

Add a deterministic schedule-driven conformance harness that can delay, duplicate, reorder where transport permits, drop, and replay protocol messages; delay or stop decoded PCM consumption; disconnect either channel; restart worker or Android state; and inject cancellation at every transition. Record content-free traces and automatically check them against the formal transition relation and invariants. Add soak tests with a producer faster than playout and controlled long-latency recovery. Finish with physical Pixel runs over speaker and Bluetooth, including ordinary model speech and deterministic source reading.

## Acceptance Criteria

Every modeled state and transition has implementation coverage. Schedule exploration and soak runs never exceed the configured admitted-unheard bound, never advance heard state without Android evidence, never resurrect a cancelled generation, and never leave an attempt without a defined terminal or recoverable state. Long latency creates bounded buffering or silence rather than stacked catch-up playback. Reconnect and both process-restart directions converge without duplicate or skipped acknowledged speech. Pixel evidence confirms no burst of accumulated model responses, source reading starts and advances normally, barge-in remains prompt, and speaker/Bluetooth route changes preserve the protocol. The retained report includes model-check results, trace-refinement results, maximum observed queue depth/duration, and operator observations.

## Notes

**2026-07-29T04:22:34Z**

Automated groundwork is complete on epic/rad-0tmq-complete-protocol: deterministic content-free schedules cover delay, metadata/media ordering, partition, both process restarts, background/foreground, route change and cancellation; TLC and implementation suites are green with maximum depth 1. This remains open for the explicit physical Pixel speaker/Bluetooth/soak evidence and is still dependency-gated by rad-qa5h.

**2026-07-29T04:23:47Z**

Final automated package on commit a65572b: Python 475 passed/1 skipped; Android unit/lint green; TypeScript check green; TLC 1,194 distinct states; 11 conformance schedules. Review APK /Users/ravd/agent-share/radcliffe/radcliffe-0.6.3-v14-bounded-playback-review-test.apk SHA-256 5cebacfd123fae7fec6c71406bb2c3001f02d77678f695a454c96e3d96bc81be (v3, non-debuggable, upgrade-compatible test signer). Required debug copy SHA-256 f29d742cecf54a84b2dcdb8ed7224f3a7a7307d3b9a46a250e621ed530fde836.
