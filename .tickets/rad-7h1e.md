---
id: rad-7h1e
status: closed
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


## Notes

**2026-07-18T04:47:19Z**

Implemented the deterministic agent-control action contract on 2026-07-17. Protocol v2 now defines stable action IDs, provenance, exact targets/scopes, typed delivery/configuration/session payloads, confirmation and cancellation messages, result dispositions, and stable safe rejection codes. Regenerated pinned-protoc 33.6 Python and Android Java/Kotlin bindings and added a strict matching TypeScript JSON contract. Added a capability-scoped pure Python boundary with generation checks, exact idempotent replay, changed-replay rejection, precedence-slot conflict detection, snippet > turn > session > device > operator-default selection, confirmation authorization, cancellation tombstones, and content-free audit fields. The schema has no generic setting, arbitrary JSON, filesystem, URL, command, environment, credential, secret, or process field. The spec and Protocol README define confirmation, precedence, replay/retry, cancellation, audit, and compatibility semantics. Conformance covers malformed, unauthorized, stale, duplicate, conflicting, unknown-forward action, and schema escape-hatch cases; protobuf tests prove unknown additive fields survive forwarding. Final make check passes: 163 Python passed/1 skipped, 26 Sidecar tests, Android unit/lint and regenerated binding compilation, Ruff, formatting, and mypy.
