---
id: rad-fdtf
status: closed
deps: [rad-gf5x, rad-i6i7]
links: [rad-f08j, rad-rmfz]
created: 2026-07-20T03:03:16Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-erac
tags: [radcliffe, jobs, tools, cancellation, anthropic, truthfulness]
---
# Require actual job mutation before claiming success

In the physical Pixel session, the user asked to cancel the active 60-second job. Haiku emitted no manage_job tool call and spoke that the job was cancelled, but the durable record had no cancellation request and the executor completed successfully.

## Design

Strengthen the model contract and interception boundary so status, follow-up, priority, and cancellation claims are grounded in a successful manage_job result. A mutation request that produces no tool call, invalid arguments, or a failed tool must not generate success language. Preserve safe reference resolution for phrases such as that job without exposing or speaking the exact ID.

## Acceptance Criteria

A retained regression reproduces the observed cancel request with an active job and zero tool calls; Radcliffe either executes manage_job cancel successfully or clearly says it did not change the job, never claiming cancellation. Successful cancel persists cancel_requested and reaches a terminal cancelled event without a later succeeded event; status, follow-up, and priority claims obey the same grounding rule; local and Haiku tests cover omitted, malformed, failed, replayed, and successful calls; telemetry remains content-free; and a physical Pixel cancellation test confirms the job stops.


## Notes

**2026-07-20T04:49:46Z**

Closed from the retained interception regressions, durable executor/API tests, later successful phone evidence identified in the autonomous handoff, and a fresh deployed cancellation smoke. Sidecar tests reproduce the exact zero-tool-call false cancellation and force fail-closed language; cover forced local/OpenAI and Anthropic tool choice, mismatched operations, gateway failure, successful cancellation/follow-up/priority/status, ID privacy, and useful grounded context. Control/executor tests cover replay and committed cancellation beating late success. On deployed revision 2e80631, a real local-Ollama sandbox job entered a shell progress step, manage_job persisted cancelRequested=true, terminal events were accepted→started→progress→cancellation_requested→cancelled with no succeeded event, and all Docker containers/networks were removed.
