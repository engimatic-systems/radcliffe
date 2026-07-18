---
id: rad-4myy
status: closed
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


## Notes

**2026-07-14T12:16:46Z**

2026-07-14 implemented on feat/context-budget-accounting. GET /v1/sessions/{id}/context-usage replays the current context-v2 packets and returns content-free model ID, context limit, projected input, reserved output, remaining capacity, percentage, exactness/basis, and overflow. The Sidecar captures the exact Pi provider payload, obtains authoritative Ollama prompt_tokens with a cached one-token loopback preflight, and prevents real generation on overflow; truncation/rejection is honestly labeled an inexact lower bound. Golden tests cover exact serialized roles/tools, CALL and TEXT_SOURCE visibility, interruption exclusion, history/model changes, and pre-provider rejection. Local deployed evidence: radcliffe-demo=1,356 input / 40,960 limit with 8,192 reserved (4.14%); make check passes 55 Python, 16 Sidecar, and Android gates; full live voice remains green at 2.529s first audio.
