---
id: rad-pb5a
status: open
deps: [rad-pm6h]
links: []
created: 2026-07-28T21:37:55Z
type: feature
priority: 0
assignee: coopwilliams
parent: rad-0tmq
tags: [radcliffe, protocol, protobuf, voice, playback, android, compatibility]
---
# Encode playback credit and reconciliation in Protocol v2

Implement the formally specified shared wire contract so worker and Android can exchange bounded playout credit and reconcile outstanding attempts without treating audio publication, timers, or transport reconnect as evidence of hearing.

## Design

Extend Protocol v2 only with fields and messages justified by the approved formal model: capability or version negotiation, receiver credit or capacity, immutable playback-attempt admission, monotonically ordered acknowledgement evidence, cancellation or flush correlation, and reconnect snapshots. Preserve stable operation, generation, playback-attempt, segment, and boundary identities. Specify replay and compatibility behavior for an older client or worker; unsupported peers must fail closed or use an explicitly bounded compatibility path rather than silently reverting to unbounded enqueueing.

## Acceptance Criteria

The protobuf schema and generated Kotlin/Python bindings represent every modeled transition without overloaded sentinel values. Decode and validation tests reject malformed bounds, identity mismatches, backwards counters, impossible terminal transitions, and unsupported negotiation. Duplicate messages are idempotent and stale generations are representable as explicit non-mutating outcomes. Compatibility tests cover old-client/new-worker and new-client/old-worker behavior, and the protocol documentation maps every field to its authority and invariant.

## Notes

**2026-07-29T01:07:50Z**

First additive wire slice implemented: Reconnect now carries max_inflight_attempts and max_segment_duration_ms; PlaybackReconciled supplies an explicit reconnect barrier. Full formal-model capability negotiation/restart compatibility remains open.
