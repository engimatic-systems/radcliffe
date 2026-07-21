---
id: rad-uk3f
status: in_progress
deps: []
links: [rad-nhai, rad-l386, rad-8ai7]
created: 2026-07-21T14:35:08Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-nhai
tags: [radcliffe, background-agent, context, events, voice, regression, operator-final-check]
---
# Persist spoken job outcomes in chronological model context

The media-worker speaks automatic background-job announcements such as 'A background job failed,' but does not append those harness utterances to the durable envelope chain. The next user message is therefore projected without the announcement immediately before it, so the conversational model does not know that the job failed or which job event caused the announcement.

## Design

Persist every successfully delivered harness job announcement as an exact SYSTEM envelope before releasing foreground ownership, and persist a separate model-only bounded job outcome record with the exact job ID, terminal status, public failure code, and instruction to use manage_job for result details. Preserve sequence ordering under concurrent user speech, deduplicate across restart, and do not claim interrupted or failed playback was fully spoken. Include the latest terminal event and promoted failure summary in manage_job status results.

## Acceptance Criteria

A completed spoken announcement appears exactly once in durable replay and model context; its outcome record precedes the next user envelope even when speech arrives immediately; interrupted announcements are not recorded as fully spoken; a failed job identifies the correct internal job and bounded failure reason without exposing private instructions or transcripts; asking why after the automatic failure lets the model answer or invoke manage_job with no user restatement; restart/replay is idempotent; deterministic pipeline, notification, projection, and tool-status tests pass.


## Notes

**2026-07-21T14:35:25Z**

2026-07-21 physical Haiku trace ses_ece6459346fbe944ae5be96736984b1c: job job_b2c386d6c3e04be43cbe5be9e680b184 failed at revision 16 with detail_code=resource_exhausted and delivery_sequence=141. The durable envelope stream contains no JobEvent or harness-announcement entry between the user's earlier request and later 'Do you know why?' turn. The worker spoke the allowlisted failure phrase through foreground audio only; context_replay therefore could not project it. The private job transcript shows repeated failed commands and no terminal explanation.

**2026-07-21T15:00:04Z**

Failure plumbing now distinguishes background model rate limits, provider outages, rejected requests, invalid responses, and generic trusted-executor faults. These stable terminal detail codes flow through the existing durable hidden job-outcome envelope and manage_job latestEvent, so the next foreground turn can explain or inspect the failure without transcript disclosure.
