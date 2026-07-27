---
id: rad-hkvx
status: open
deps: []
links: []
created: 2026-07-25T17:45:19Z
type: bug
priority: 2
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, tools, authorization]
---
# Investigate first-attempt read_source denial on promoted job sources

Reads of promoted background-job sources are denied on first attempt and allowed on an immediate retry at the same generation, costing a round trip and an audible stall.

## Design

In ses_853b2faaef57834a4319a00bd0adb0f6 the tool_calls audit shows paired denied/allowed read_source rows at identical generations: toolu_01EkrtF7 denied then toolu_01T2SuBv allowed (gen 3); toolu_01HNqz2S denied then toolu_01AQBg allowed (gen 10); toolu_011EEomC denied then toolu_01X51S2k allowed (gen 11). Generation is stable across each pair, so this is not generation staleness. All denials are on var/jobs/... promoted paths. Suspect grant issuance or readable_roots attachment timing around an interrupt; root cause not yet established.

## Acceptance Criteria

The denial is reproduced deterministically in a test; the root cause is identified rather than retried around; an authorized promoted job source is readable on first attempt; and the failure mode, if it remains reachable, surfaces a specific error instead of a generic permission denial.

