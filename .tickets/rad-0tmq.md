---
id: rad-0tmq
status: in_progress
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

## Notes

**2026-07-28T21:56:44Z**

Operator authorized tackling the bounded playback state-machine work as one epic on one branch and one PR. Execution begins with rad-fnho to measure and map the real buffering and acknowledgement seams; formal specification and production implementation remain dependency-gated on that evidence.

**2026-07-29T01:07:50Z**

Phase C temporary-disconnect vertical slice implemented on the epic branch: receiver window fixed at one attempt, 30-second segment cap, Android reconnect snapshot, worker flush/reconciliation, same-segment/new-attempt replay, and a playback-track readiness barrier. The checked-in safety argument is docs/bounded-playback-reconnect.md. This does not close the epic or claim process-restart durability/model checking.

**2026-07-29T01:10:43Z**

Automated verification for the Phase C slice: full Python suite 466 passed/1 skipped; focused protocol/media-worker suite 170 passed; Android full unit/lint green; TypeScript compilation green. Aggregate make check reaches only the pre-existing agent-sidecar prompt test mismatch (the user changed 'without a preamble' to uppercase while the test still asserts lowercase); that unrelated dirty prompt file remains untouched/uncommitted. Operator artifacts: /Users/ravd/agent-share/radcliffe/radcliffe-0.6.2-v13-phase-c-review-test.apk (SHA-256 f13cfe0f36c99a8f217a5a4d4568da448ebd3d98ea55b272c86c89aaf4d627ed; same 4ac1d019 test signer as installed v12; aligned, v3, non-debuggable) and required debug copy /Users/ravd/agent-share/radcliffe/radcliffe-0.6.2-v13-phase-c-debug.apk (SHA-256 b3af494e6877c3bc86a56c67c3a52cf0fef669b6f8f9b5056bc0ae28ef59da7d). Physical Phase C remains pending.

**2026-07-29T02:17:05Z**

Physical Phase C reconnect test passed on 2026-07-28: operator cut the connection immediately after hearing 'four'; after reconnect playback resumed with 'four' rather than skipping or duplicating an earlier completed segment. Follow-up trace profiling found completion-to-next-start gaps of 1.3-1.6 seconds: 0.6-0.95 seconds was next-segment TTS because model speech still synthesized behind the acknowledgement gate, with the remainder in device/data/media handoff. Model speech now keeps exactly one synthesized local segment ahead while retaining the one-attempt receiver admission window; automated UAT is green and physical gap UAT is pending.
