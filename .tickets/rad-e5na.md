---
id: rad-e5na
status: closed
deps: []
links: [rad-mz5a]
created: 2026-07-17T23:53:29Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, anthropic, haiku, model, cost, privacy, debug]
---
# Switch the debug live-testing profile to budget-capped Claude Haiku 4.5

Use Claude Haiku 4.5 as the temporary cloud-backed debugging and live-testing model, retaining an explicit local profile and a deliberate production upgrade path to a more capable model or router.

## Design

Pin the Anthropic model snapshot, bridge the protected Radcliffe key into the provider process without duplicating it, and keep cloud activation explicit. Bound output and session spend, make provider/model visible in diagnostics, and implement truthful Anthropic context accounting so the session indicator does not regress to a misleading value.

## Acceptance Criteria

The live profile selects Anthropic with the pinned Claude Haiku 4.5 model ID and requires the explicit cloud opt-in; the funded key remains owner-only, outside logs, child-process listings, artifacts, and model-visible tools; per-turn output and cumulative session spend have conservative configurable ceilings with a visible exhaustion result; exact provider token counting or an explicitly labeled conservative estimate keeps context accounting truthful; model/provider changes create a new accounting basis; cloud transmission is disclosed and local Ollama remains an explicit selectable fallback rather than a silent privacy-changing failover; cancellation, tools, source rendering, no-emoji policy, interruption, and first-audio gates pass; and one user-authorized bounded paid smoke records token usage and estimated cost without storing prompt content.


## Notes

**2026-07-18T02:30:35Z**

Claude Haiku 4.5 is a temporary debugging and live-testing profile chosen for speed and cost; production Radcliffe is expected to use a more capable model or model-routing policy.

**2026-07-18T06:43:22Z**

Implemented the non-billable live-debug profile: pinned claude-haiku-4-5-20251001 and reviewed pricing/context contract; explicit cloud/local launch isolation and disclosure; owner-only single-name key bridge with Node environment removal; Anthropic count_tokens accounting labeled as a 2% plus 32-token conservative estimate; per-foreground-turn output and durable per-session USD ceilings; pseudonymous restart-safe reservation ledger; provider/model/budget diagnostics; visible model-excluded exhaustion events; and a doubly gated content-free paid-smoke recorder. Verification: make check passed (242 Python passed, 1 skipped; 39 sidecar tests passed; Android unit tests and lint passed). No Anthropic request or funded key access was performed. Ticket remains in progress pending explicit authorization for the one bounded paid smoke.

**2026-07-19T01:33:16Z**

One explicitly authorized bounded paid smoke completed successfully against Anthropic claude-haiku-4-5-20251001. Exactly one request was recorded. Provider usage: 1,242 input tokens, 11 output tokens, no cache tokens, estimated usage cost $0.001297. The conservative durable budget ledger charged $0.002598 against the $0.25 session cap and retained no reservation. The owner-only mode-0600 report records no prompt or response content and contains no protected key. The isolated cloud sidecar was stopped after verification; the local sidecar remained available.
