---
id: rad-e5na
status: open
deps: []
links: []
created: 2026-07-17T23:53:29Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, anthropic, haiku, model, cost, privacy]
---
# Switch the live agent profile to budget-capped Claude Haiku 4.5

Use Claude Haiku 4.5 as the normal cloud-backed live conversation model, retaining an explicit local profile and a deliberate Sonnet upgrade path.

## Design

Pin the Anthropic model snapshot, bridge the protected Radcliffe key into the provider process without duplicating it, and keep cloud activation explicit. Bound output and session spend, make provider/model visible in diagnostics, and implement truthful Anthropic context accounting so the session indicator does not regress to a misleading value.

## Acceptance Criteria

The live profile selects Anthropic with the pinned Claude Haiku 4.5 model ID and requires the explicit cloud opt-in; the funded key remains owner-only, outside logs, child-process listings, artifacts, and model-visible tools; per-turn output and cumulative session spend have conservative configurable ceilings with a visible exhaustion result; exact provider token counting or an explicitly labeled conservative estimate keeps context accounting truthful; model/provider changes create a new accounting basis; cloud transmission is disclosed and local Ollama remains an explicit selectable fallback rather than a silent privacy-changing failover; cancellation, tools, source rendering, no-emoji policy, interruption, and first-audio gates pass; and one user-authorized bounded paid smoke records token usage and estimated cost without storing prompt content.

