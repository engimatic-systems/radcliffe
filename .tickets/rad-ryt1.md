---
id: rad-ryt1
status: open
deps: []
links: []
created: 2026-07-27T15:32:01Z
type: bug
priority: 2
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, context, voice, resume, tool-errors]
---
# Prevent historical tool failures from hijacking unrelated resumed turns

A resumed Sonnet 5 conversation containing several earlier manage_session failure audits answered a new sky question with another explanation of the old conflict. The failure envelopes are intentionally durable and model-visible so an immediate 'why did that fail?' follow-up works, but repeated historical failures can dominate a later unrelated request after resume.

## Acceptance Criteria

Content-safe tool failures remain durable and available to an immediate relevant follow-up, but an unrelated later user turn is answered on its own terms rather than as another request to explain a stale failure. Deterministic projection/prompt tests cover both behaviors, including multiple repeated failures followed by resume.

