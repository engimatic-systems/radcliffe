---
id: rad-7h1e
status: open
deps: []
links: []
created: 2026-07-17T23:28:12Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-1wji
tags: [radcliffe, agent-control, protocol, policy]
---
# Define the deterministic agent-control action contract

Define the versioned structured boundary through which model or tool output may request speech delivery, allowlisted configuration, and session lifecycle actions.

## Design

Specify stable action IDs, actor and message provenance, target, scope, typed arguments, generation, idempotency key, confirmation requirement, result, and disposition. Define deterministic precedence among snippet, turn, session, device, and operator defaults. Route requests through an allowlisted policy registry compatible with the planned per-message interception shim.

## Acceptance Criteria

Protocol and local spec define the supported action envelope, provenance, scope and precedence rules, confirmation matrix, retry and replay behavior, cancellation semantics, compatibility evolution, audit fields, and safe rejection codes; arbitrary setting names, secrets, filesystem access, process execution, and unregistered actions are impossible through this boundary; user, model, call, and text-source origins are distinguishable; generated Kotlin, Python, and TypeScript bindings or equivalent typed contracts compile; and conformance tests cover malformed, unauthorized, stale-generation, duplicate, conflicting, and forward-compatible actions.

