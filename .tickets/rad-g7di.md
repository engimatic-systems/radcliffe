---
id: rad-g7di
status: open
deps: [rad-wnwh, rad-58mt, rad-0ekb]
links: []
created: 2026-07-13T01:37:25Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, tools, pi]
---
# Spike Pi Agent Core behind the AgentOrchestrator interface

Prove that the version-pinned Pi Agent Core can provide Radcliffe's typed model/tool loop without owning state, context policy, or capability execution.

## Acceptance Criteria

Pi runs as a Node 22.19+ sidecar behind a documented AgentOrchestrator interface; Radcliffe ContextPackets map to Pi custom messages and Pi events map back to canonical MODEL and CALL envelopes; transformContext applies the Radcliffe ContextProjection; exact package versions are pinned; sequential typed tool calls, streamed updates, cancellation propagation, and process restart reconstruction pass contract tests; the spike documents an adapter replacement path.
