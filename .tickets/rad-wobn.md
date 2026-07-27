---
id: rad-wobn
status: in_progress
deps: []
links: [rad-orob, rad-1w52]
created: 2026-07-25T15:19:17Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, cloud, budget, foreground, background-agent, operator-final-check]
---
# Remove the foreground session dollar cap and enforce a per-job Agent SDK budget

The $0.25 CloudBudgetController was applied to the whole foreground Radcliffe conversation, eventually preventing Haiku responses and therefore TTS. The intended scope was each background job.

## Design

Remove dollar-budget enforcement from the foreground sidecar while retaining usage telemetry, context limits, and per-turn output limits. Configure every background Agent SDK job with an independent $0.25 maximum model cost.

## Acceptance Criteria

Foreground Haiku calls are not rejected because of accumulated session dollar spend; usage remains observable; each background job begins with its own $0.25 cap and reports budget exhaustion only for that job; configuration and regression tests cover both boundaries.


## Notes

**2026-07-25T15:36:40Z**

2026-07-25 implementation is locally green. The foreground sidecar no longer constructs or enforces CloudBudgetController; context preflight, per-call output limits, and usage events remain. Agent SDK CLI/default profile and active ignored operator configuration now cap each job independently at $0.25 and pin Haiku. Full Python suite: 372 passed, 1 skipped; Node: 106 passed; Ruff and mypy passed.
