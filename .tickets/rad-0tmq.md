---
id: rad-0tmq
status: open
deps: []
links: [rad-g9yr, rad-ig6k, rad-u6lm]
created: 2026-07-28T21:37:14Z
type: epic
priority: 0
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, voice, playback, android, protocol, reliability, formal-methods, operator-final-check]
---
# Bound end-to-end audio flow with an owner-explicit playback protocol

Audio can currently be synthesized and admitted faster than the Android sink consumes it. Model and canonical-source segments can therefore accumulate across processing delay or network jitter, play in a burst, time out before starting, or leave stale acknowledgements after cancellation. Replace duration guesses and effectively unbounded enqueueing with a bounded receiver-credit protocol whose ownership and recovery semantics are explicit.

## Design

The server owns canonical segment order, session generation, operation cancellation, immutable playback-attempt issuance, and the durable ledger. Android owns decoded-PCM consumption facts, local queue capacity, route-local flush completion, and the credit it grants for additional playout. The server may send audio only within current Android-granted credit and may advance a heard/source boundary only from valid Android evidence. LiveKit transports media and data but owns no product state. Every command, acknowledgement, retry, duplicate, stale generation, disconnect, reconnect, process restart, and timeout must have a defined idempotent transition. Derive the final protocol from measured buffering seams, specify it as a total state machine, and machine-check its safety and conditional-liveness properties before implementing it.

## Acceptance Criteria

All child tickets are complete. The implemented protocol places a documented finite upper bound on admitted but unheard audio; does not burst an accumulated response after delay or reconnect; never advances heard state from server clocks or enqueue completion; never allows stale, duplicate, reordered, or cross-generation events to mutate current playback; and converges after reconnect or fails closed with an explicit terminal state. A machine-checked model and human-readable inductive correctness argument cover every state/event pair. Automated chaos/conformance and physical Pixel tests cover ordinary model speech, deterministic source reading, pause, barge-in, route change, process restart, and network partition without duplicated or skipped acknowledged speech.
