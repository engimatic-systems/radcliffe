---
id: rad-1wji
status: closed
deps: []
links: []
created: 2026-07-17T23:28:02Z
type: epic
priority: 1
assignee: coopwilliams
tags: [radcliffe, agent-control, configuration, voice, sessions]
---
# Conversationally control Radcliffe behavior and session lifecycle

Let the user ask the active agent to change how Radcliffe behaves, apply bounded delivery instructions to individual spoken snippets, and move into a fresh or existing conversation through explicit deterministic actions.

## Design

Treat model output as a request to a versioned allowlisted action boundary, never as authority to mutate arbitrary application state. Every action declares its target, scope, source, idempotency key, confirmation policy, and observable result. The same interception shim planned for deterministic message inspection can evaluate these requests before they reach Android, TTS, or session control.

## Acceptance Criteria

A spoken user request can produce a validated action at snippet, turn, session, or device scope; only registered actions and bounded values are accepted; applied changes are visible, reversible, and auditable without storing secrets or private message content; per-snippet delivery never alters canonical spoken text; session transitions preserve history and leave no orphaned work; deterministic fake and local-provider tests cover authorization, precedence, replay, cancellation, reconnect, and failure recovery; and Android exposes the resulting state without requiring hidden configuration.


## Notes

**2026-07-17T23:53:05Z**

2026-07-17 design clarification: keep the normal model-visible control surface small. Prefer one manage_session tool with a discriminated action, one typed configure tool, and a delivery.rate argument on read_source/read-snippet calls with 1x as the default. Ordinary generated speech needs no tool call at 1x; exceptional per-snippet delivery should be ephemeral RenderProjection metadata consumed by the interception shim and omitted from later ContextProjection. CALL envelopes remain auditable, but low-value control calls/results should not accumulate in future model context.

**2026-07-20T05:01:24Z**

All five child tickets are closed as of 48a2d29. The completed implementation has typed allowlisted snippet delivery, configuration set/inspect/reset/undo, confirmed session create/switch/restart/end, compact Android effective-state controls, durable audit records, replay/idempotency and cancellation handling, and session handoff cleanup. The repository-wide deterministic gate passes: Python 305 passed/1 skipped (including configuration API, session-action API, media pipeline, restart and failure recovery), Node 55 passed (agent tool interception/local-provider orchestration), and Android unit/lint passed.
