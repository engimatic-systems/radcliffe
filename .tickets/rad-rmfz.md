---
id: rad-rmfz
status: in_progress
deps: []
links: [rad-i6i7]
created: 2026-07-20T01:53:18Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-erac
tags: [radcliffe, background-agent, tools, schema, qwen, executor, observability]
---
# Make the background Unix agent robust to invalid batched tool calls

The first physical-phone coding job was accepted and started but the local Qwen background executor failed immediately after creating its script because a batched run_shell call used an unsupported purpose and the later finish_job call supplied an absolute artifact path.

## Design

Validate every model-produced background tool call before side effects, expose provider-compatible schemas, and recover safely from near-valid non-destructive arguments through bounded correction or retry. Execute dependent calls sequentially, never accept premature finish_job claims, require workspace-relative artifacts, and record a private content-safe failure reason instead of collapsing all validation faults into executor_failed.

## Acceptance Criteria

A deterministic test reproduces one response containing a valid file-writing run_shell call, a second run_shell with purpose executing, and finish_job with an absolute /workspace path; the agent does not terminate generically or claim success before executing and checking the script; invalid arguments receive a bounded corrective retry or a specific terminal code; artifact paths are normalized only when unambiguous and remain inside the job workspace; phone-initiated Fibonacci work reaches a verified result and visible Android completion; private diagnostics identify the failed call without leaking instructions, commands, or output.


## Notes

**2026-07-20T01:53:59Z**

2026-07-19 private executor evidence: Qwen returned three calls in one response. It first wrote fibonacci_script.py successfully, then requested run_shell with purpose=executing, which is outside the allowed purpose enum and raised validation, and finally proposed finish_job before execution with artifactPaths=[/workspace/fibonacci_script.py]. The exception boundary emitted only executor_failed, obscuring the actionable cause.

**2026-07-20T02:36:39Z**

2026-07-19 deployed revision b0f090c. The exact batched-call regression is covered deterministically; the live local Qwen executor completed job_09b48ee8d336dea954254a8a0df4d00a on its first attempt with coding and testing progress and a verified Fibonacci artifact. Host-side and deployment gates pass; keep open until the user confirms a phone-initiated job and visible Android completion.
