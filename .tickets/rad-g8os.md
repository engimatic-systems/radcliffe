---
id: rad-g8os
status: open
deps: [rad-pb5a]
links: []
created: 2026-07-28T21:38:09Z
type: feature
priority: 0
assignee: coopwilliams
parent: rad-0tmq
tags: [radcliffe, media-worker, voice, playback, backpressure, reconnect]
---
# Enforce receiver credit and bounded audio admission in the media worker

Replace worker-side duration sleeping and eager segment publication with the server half of the approved credit protocol. Synthesis may run ahead only within a separately documented local bound; audio must not enter LiveKit or become a device attempt without receiver capacity.

## Design

Implement one serialized playback actor per session generation. It owns canonical ordering, attempt issuance, cancellation, the sender credit ledger, and durable acceptance of device evidence. Admit audio only when the formal credit predicate holds; return or retire credit only on the modeled receiver transition. Use Android started/position/terminal evidence and protocol-defined progress watchdogs instead of server estimates of physical completion. Keep model and deterministic-source delivery on the same admission path while preserving source boundary and heard-prefix rules. Reconcile from the durable ledger and Android snapshot after worker or transport restart.

## Acceptance Criteria

At all observable points, outstanding admitted audio satisfies the protocol bound and the implementation exposes a content-free gauge proving it. A fast model or synthesizer cannot create an unbounded LiveKit/device backlog. A delayed start does not time out merely because prior admitted audio is still playing; missing progress follows the specified failure transition. Cancellation prevents any further old-generation admission and safely reconciles terminal residue. Restart and reconnect tests restore or explicitly flush outstanding attempts without duplicate heard-boundary advancement. Unit tests and a trace-refinement test show worker transitions correspond to the machine-checked model.

## Notes

**2026-07-29T01:07:50Z**

First sender slice implemented: model/source/job speech share a one-attempt admission window, model speech waits for Android terminal evidence, reconnect supersedes only the uncertain transport attempt and retries the stable segment before later speech. Durable restart recovery remains open.
