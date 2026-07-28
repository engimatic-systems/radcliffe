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
