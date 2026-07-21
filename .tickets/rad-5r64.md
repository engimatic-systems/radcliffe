---
id: rad-5r64
status: open
deps: []
links: [rad-8akg]
created: 2026-07-21T18:15:46Z
type: feature
priority: 1
assignee: coopwilliams
tags: [radcliffe, background-agent, retry, follow-up, voice, operator-final-check]
---
# Retry a failed background job with an amended instruction

A user asked to retry a failed job while adding a print statement. Plain retry had already succeeded, but this amended retry was routed to a terminal follow-up and returned a state conflict. The current retry operation deliberately reuses the original instruction and cannot atomically append a bounded amendment.

## Design

Allow an optional bounded instruction amendment on retry, or define a separate retry_with_follow_up operation. Persist the amended input with hash and provenance, keep the same job identity and bounded attempt count, and reject ambiguous or replay-conflicting mutations.

## Acceptance Criteria

A failed job can be retried by voice with an additional instruction in one idempotent command; the next attempt receives the original instruction plus the amendment in deterministic order; replay is safe; plain retry behavior is unchanged; terminal follow_up remains rejected; Pixel verification produces the requested amended side effect without creating a duplicate job.


## Notes

**2026-07-21T18:15:46Z**

Observed in physical session ses_b97423f51c8b64a498bfea2144ddd9ff after successful plain retry. The utterance requested another retry with a hello-world print statement; manage_job returned conflict and the next turn correctly retained and explained that content-safe tool failure.
