---
id: rad-tb0q
status: closed
deps: []
links: []
created: 2026-07-27T15:16:09Z
type: chore
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, milestone-1, conformance, sessions, sonnet]
---
# Refresh session conformance for heard-only sources and Sonnet accounting

The M1 closeout run of make check-session-local exposed two stale July 16 assertions. Since c5a3bce the Tool Gateway transfers canonical source privately and only the media worker persists client-heard prefixes, so a direct tool call must not preemptively add a text_source timeline item. The pinned Sonnet 5 profile reports conservative Anthropic count_tokens accounting rather than the old Ollama exact=true basis.

## Acceptance Criteria

The isolated session conformance suite validates byte-exact canonical source and visible provenance on the transferred envelope, proves a direct tool call does not persist unheard source text, accepts only the documented exact Ollama or conservative Anthropic accounting bases with positive bounded usage, and completes green under the pinned Sonnet 5 service profile.


## Notes

**2026-07-27T15:17:14Z**

Implemented and verified 2026-07-27. Session conformance now validates transferred canonical bytes/hash/provenance, asserts that a direct Tool Gateway call does not persist unheard source text, verifies the unchanged audit timeline across control-plane restart, and accepts the explicitly bounded Ollama-exact or Sonnet 5 conservative Anthropic accounting contracts. Fresh isolated report artifacts/session-conformance/20260727T151705Z/report.json passes 29/29 under the pinned Sonnet 5 service profile.
