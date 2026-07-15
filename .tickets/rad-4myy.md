---
id: rad-4myy
status: open
deps: [rad-214n]
links: [rad-0ekb]
created: 2026-07-14T02:48:09Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, milestone-2, context, projection, api]
---
# Expose deterministic per-session context budget accounting

Compute and expose how much of the configured model context window the deterministic session projection consumes.

## Design

Measure the exact ContextProjection sent to the selected model using a tokenizer or provider accounting contract pinned to the model configuration. Keep used input, reserved output, limit, projection version, and measurement basis explicit; do not present character-count guesses as exact tokens.

## Acceptance Criteria

For every session the control plane exposes context limit, projected input usage, reserved output budget, remaining budget, percentage, projection version, model ID, and whether the count is exact or estimated; interrupted or excluded envelopes follow the documented projection policy; tool calls and canonical text-source envelopes are counted according to their actual model-visible representation; values update deterministically after append, interruption, compaction, and model change; overflow is detected before provider submission with an actionable error; no message content is leaked by the metrics endpoint; and golden tests compare API accounting to the exact serialized model request.

