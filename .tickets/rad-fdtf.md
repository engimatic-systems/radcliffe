---
id: rad-fdtf
status: in_progress
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

