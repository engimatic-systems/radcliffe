---
id: rad-lo0t
status: open
deps: []
links: [rad-rwfl, rad-chaa, rad-214n, rad-ghti, rad-nu8n]
created: 2026-07-14T04:05:29Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-3se2
tags: [voice, transcript, reliability]
---
# Persist and publish audible partial model output exactly once

A model response can be audible on the phone but absent from the Android transcript when the turn is interrupted or fails during streaming TTS. The July Pixel run produced a long audible response followed by a TTS failure with no MODEL envelope in the UI.

## Design

On interruption or failure, persist the accumulated model text before its terminal disposition and publish the same envelope to connected clients. Preserve rendered-boundary and partial metadata, guard persistence with the turn identity, and keep failed or interrupted output out of future model context.

## Acceptance Criteria

Audible accumulated model text is persisted and published for completed, interrupted, and failed turns; each logical model response produces no duplicate MODEL envelope; disposition metadata makes partial versus completed reconstruction deterministic; no unheard speculative tail is presented as rendered; failed and interrupted output remains excluded from subsequent LLM context; tests cover the observed heard-then-failed response and an interrupted partial response.

