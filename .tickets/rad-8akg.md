---
id: rad-8akg
status: closed
deps: []
links: [rad-uk3f, rad-l386, rad-5r64]
created: 2026-07-21T15:19:35Z
type: bug
priority: 0
assignee: coopwilliams
tags: [radcliffe, background-agent, retry, haiku, tools, operator-final-check]
---
# Retry terminal background jobs and transient Haiku preflights

The foreground model maps a user's explicit retry request to manage_job follow_up, which conflicts once the job is terminal. Separately, a transient Anthropic context-accounting failure aborts the entire foreground turn before any response, and background provider retries recover for only seven seconds.

## Design

Add an explicit idempotent retry operation that requeues a failed job as a fresh bounded attempt using the same instruction. Detect direct retry language and force that operation. Retry transient Anthropic count-token and background model failures with bounded asynchronous backoff, while preserving stable failure codes and user cancellation.

## Acceptance Criteria

An observed failed job can be retried by voice without a conflict or a duplicated job record; the next claim increments attempt and preserves event ordering; succeeded or running jobs reject retry; transient context-accounting and provider failures recover in deterministic tests; exhausted retries remain typed and visible.


## Notes

**2026-07-21T15:33:18Z**

2026-07-21 physical trace: the direct retry request was forced into follow_up against terminal job_3c274297658316ff028879eb83febc74 and received a conflict. A later fresh-launch foreground turn failed in roughly 160 ms when the Anthropic count_tokens preflight returned 503; another background attempt exhausted four provider requests after only about seven seconds. Implemented an idempotent retry command for failed jobs with at most three total attempts, explicit voice retry routing, four transient context-accounting attempts, a safe media-worker retry for pre-stream 503, and six background provider attempts with about 31 seconds of bounded backoff. Deterministic retry, migration, and transient recovery tests pass.

**2026-07-21T15:54:25Z**

2026-07-21 Pixel session did not exercise retry acceptance. The requested intentional ZeroDivisionError command exited 1, but the overall instruction was to demonstrate that expected error; Haiku correctly treated that goal as completed and called finish_job, so job_d524c1e0d88594339fcf4409bfdfc579 ended succeeded. A physical test still needs a job instructed to call fail_job, followed by the user saying retry that job.

**2026-07-21T18:15:46Z**

2026-07-21 final Pixel acceptance ses_b97423f51c8b64a498bfea2144ddd9ff: job_0efa74a5915de25cb0e8e70dec72b901 failed at revision 4 with task_failed; the direct voice command Retry that job created one retry command at revision 5 on the same job ID; the executor claimed attempt 2 at revision 6 and failed as instructed at revision 8. No duplicate job and no conflict occurred. The second failed terminal announcement and hidden outcome were persisted at sequences 14 and 15. Physical retry acceptance passes.
