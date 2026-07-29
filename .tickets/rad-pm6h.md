---
id: rad-pm6h
status: closed
deps: [rad-fnho]
links: []
created: 2026-07-28T21:37:39Z
type: task
priority: 0
assignee: coopwilliams
parent: rad-0tmq
tags: [radcliffe, voice, playback, protocol, formal-methods, correctness]
---
# Specify and machine-check the bounded playback ownership protocol

Turn the measured audio path into a total distributed protocol before production implementation. The design must eliminate ambiguous authority: the worker decides canonical order and issuance, Android reports actual playout and grants bounded capacity, the durable ledger records accepted evidence, and transport delivery never implies playback.

## Design

Write a human-readable protocol RFC and a machine-checkable state model, preferably TLA+ with a pinned TLC invocation unless the spike demonstrates a better exhaustive formalism. Model at least server and Android process restart, data duplication/loss/reordering where applicable, media delay/loss, arbitrary finite latency, disconnect/reconnect, retry, stale generations, cancellation, route-local flush, and bounded credit. Define a total transition function: every state/event pair must accept, idempotently replay, explicitly ignore, or reject without mutation. State environmental fairness assumptions separately from safety. Map each abstract action and variable to concrete Protocol v2 messages, worker state, Android state, and durable rows.

## Acceptance Criteria

The RFC contains a complete ownership table, message grammar, state diagrams, transition table, credit conservation rule, timeout semantics, reconnect handshake, restart recovery, and failure matrix. The model checker explores configured bounds with no invariant violation or deadlock. Proved safety properties include bounded admitted-unheard audio, one current epoch, ordered single acceptance of attempts, no heard-boundary advancement without valid Android evidence, no stale resurrection, cancellation dominance, and idempotent replay. Conditional liveness states its fairness assumptions and proves that a connected progressing sink eventually completes, flushes, or explicitly fails every admitted attempt. A written inductive proof explains why initial state and every transition preserve the invariants, and documents what cannot be guaranteed during a network partition.

## Notes

**2026-07-29T04:22:34Z**

Completed on epic/rad-0tmq-complete-protocol. formal/playback/BoundedPlayback.tla plus the RFC model W=1 credit, metadata acceptance before media, reconnect, both process crashes/restarts, cancellation, reissue, and stale-generation reconciliation. Pinned TLC 1.7.4 exhaustively checks 1,194 distinct states / 3,994 generated states with no invariant violation or deadlock; the RFC contains ownership, transition/failure tables, refinement mapping, inductive safety argument, and conditional-liveness assumptions.
