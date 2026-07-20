---
id: rad-i6i7
status: open
deps: []
links: [rad-rmfz]
created: 2026-07-20T01:32:15Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, tools, schema, ollama, anthropic, observability]
---
# Harden model-facing tool calls against weak schema adherence

Make Radcliffe tool invocation reliable when a local or cloud conversational model produces near-valid arguments or cannot follow complex JSON Schema unions. Every initial, resumed, or model-directed session turn must receive the complete usable tool contract.

## Design

Generate provider-compatible model schemas and strict execution validators from one canonical tool registry, and attach the complete tool set to every provider request, including the first turn after user- or model-directed session creation, switching, or restart. Replace model-facing top-level anyOf/const-heavy schemas with flat object schemas and standard string enums. Use the Pi prepareArguments compatibility seam only for explicitly safe normalization/defaulting (for example action to operation and dropping invalid optional job controls in favor of server defaults), never for ambiguous destructive intent. Persist content-free failed-tool outcomes and expose them in readiness/diagnostics so a narrated correction cannot masquerade as a completed call.

## Acceptance Criteria

Deterministic tests reproduce qwen3:8b emitting run_job with notificationPolicy=email, priority=5, budgetUsd=0 and manage_session with action=create; the repaired adapter safely produces the intended typed calls or a clear bounded failure; run_job, manage_session, configure, and manage_job receive complete usable schemas through both the Ollama OpenAI-compatible and Anthropic adapters on initial, resumed, switched, and restarted session turns; invalid calls are visible as failed tool events without prompts or raw arguments in telemetry; a deployed local-model smoke creates a real background job and a Haiku schema probe covers the cloud profile.

## Notes

**2026-07-20T01:53:59Z**

2026-07-19 phone evidence under Haiku: run_job received a usable object schema and accepted the job, but repeated manage_job attempts failed before persistence because the Anthropic adapter had reduced its top-level union schema to an unusable shape. The user requires the full usable tool contract whenever a session begins or changes.
