---
id: rad-e0qf
status: open
deps: [rad-4myy, rad-e5na]
links: [rad-7ur1]
created: 2026-07-18T02:30:35Z
type: feature
priority: 3
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, android, cost, models, context, ux]
---
# Estimate cold-cache conversation resume cost by model

When choosing a recent conversation and model, show an honest estimate of the API cost to resume that conversation with a cold provider cache and complete one representative turn.

## Design

Use the exact projected context count when available, a versioned model-pricing registry, provider-specific cold-cache write or uncached-input rules, and a clearly stated expected-output assumption. Present a range or qualified estimate rather than false precision. Local models have zero API-token cost but may expose latency separately in a future feature.

## Acceptance Criteria

Each available cloud model choice can show an estimated cold-cache resume cost before connection; the estimate includes the conversation's projected input, provider cache-write or uncached-input rate, tool/system overhead included by the measured provider payload, and a configurable representative output allowance; long-context pricing tiers and promotional expiration dates are handled explicitly; model ID, currency, pricing effective date, cache assumption, token basis, and output assumption are available in details without crowding the row; unavailable or inexact accounting is labeled and never shown as exact; local models show no API charge rather than a fabricated dollar cost; pricing data is testable and updateable without an app release where practical; the estimate makes no billable provider call; and boundary tests cover empty, large, overflow, cold/warm, tiered, expired-price, unknown-model, and model-switch cases.

