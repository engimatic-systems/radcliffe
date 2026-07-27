---
id: rad-ryt1
status: in_progress
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


## Notes

**2026-07-27T15:49:41Z**

2026-07-27 implementation: persisted non-assistant packets must be represented as user-role messages at the provider boundary, and tool failures previously carried only the generic label '[Radcliffe system]'. In the resumed trace this made a past conflict look too much like a live conversational topic even though a new user request followed it. Tool-failure packets now retain their content-safe audit detail for relevant follow-ups but are explicitly labeled as historical past events, not pending requests. A conditional system instruction says to use them when the current user asks about or continues the failure, and to answer an unrelated topic change directly without repeating the old error. Regressions cover an immediate 'why did that fail?' follow-up and two repeated failures followed by an unrelated resumed sky question. Full make check passes: 464 Python passed/1 skipped, 114 sidecar tests, Android unit/lint.
