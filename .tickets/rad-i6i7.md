---
id: rad-i6i7
status: open
deps: []
links: []
created: 2026-07-20T01:32:15Z
type: bug
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, tools, schema, ollama, anthropic, observability]
---
# Harden model-facing tool calls against weak schema adherence

Make Radcliffe tool invocation reliable when a local or cloud conversational model produces near-valid arguments or cannot follow complex JSON Schema unions.

## Design

Replace model-facing top-level anyOf/const-heavy schemas with provider-compatible flat object schemas and standard string enums while retaining strict typed validation at the execution boundary. Use the Pi prepareArguments compatibility seam only for explicitly safe normalization/defaulting (for example action to operation and dropping invalid optional job controls in favor of server defaults), never for ambiguous destructive intent. Persist content-free failed-tool outcomes and expose them in readiness/diagnostics so a narrated correction cannot masquerade as a completed call.

## Acceptance Criteria

Deterministic tests reproduce qwen3:8b emitting run_job with notificationPolicy=email, priority=5, budgetUsd=0 and manage_session with action=create; the repaired adapter safely produces the intended typed calls or a clear bounded failure; run_job, manage_session, configure, and manage_job schemas work through both the Ollama OpenAI-compatible and Anthropic adapters; invalid calls are visible as failed tool events without prompts or raw arguments in telemetry; a deployed local-model smoke creates a real background job and a Haiku schema probe covers the cloud profile.

