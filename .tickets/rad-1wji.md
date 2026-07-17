---
id: rad-1wji
status: open
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

