---
id: rad-25m0
status: closed
deps: []
links: [rad-e9wi, rad-rnt5, rad-u96k]
created: 2026-07-22T02:30:39Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, tools, anthropic, voice, operator-final-check]
---
# Prevent textual foreground tool calls from reaching speech

Foreground models can occasionally serialize XML-like function_calls/invoke text as ordinary assistant content. Radcliffe must never execute or speak that imitation; explicit job-launch intent should force the provider structured run_job channel, and an unexpected textual protocol response should be discarded and retried once through the structured channel.

## Design

Force run_job on explicit background-job launch turns using provider-native tool choice. Detect textual tool protocol before speech/display emission, discard it, and enqueue one bounded corrective follow-up. Preserve explicit verbatim-output requests.

## Acceptance Criteria

Deterministic streaming tests prove XML-like tool syntax never reaches model_delta or model_complete, the retry invokes exactly one native run_job call, explicit job launch selects run_job through the Anthropic toolChoice object, repeated protocol failure is bounded, and explicitly requested literal tags remain deliverable.


## Notes

**2026-07-22T02:31:08Z**

Implemented provider-native run_job forcing for explicit background launches plus a pre-speech textual-protocol quarantine with one structured retry. TypeScript validation passes and the sidecar suite has 102 passing tests, including literal XML leakage, exact-output exemption, and Anthropic toolChoice coverage. Leave in progress pending a phone confirmation because the original symptom was intermittent and audible.

**2026-07-22T02:56:01Z**

2026-07-21 phone regression: the first launch attempt emitted a fenced Markdown/YAML imitation beginning with run_job and job_type through the voice channel, claimed acceptance without a real tool event, and required the operator to request the tool again. The prior quarantine covered XML/function tags only. Expanded detection to fenced/plain/JSON-like internal tool syntax and added the exact foreground instruction 'Follow the schema exactly' to both the system prompt and bounded corrective retry.

**2026-07-22T03:00:26Z**

Regression coverage now uses the exact fenced form observed on the phone (run_job plus YAML-like job_type/target_url fields). The sidecar quarantines it before model_delta/model_complete, queues one retry, and the retry invokes exactly one native run_job call. The foreground system prompt and corrective retry both contain 'Follow the schema exactly'.

**2026-07-22T13:43:49Z**

Newest two phone sessions isolated a schema-lifecycle root cause, not another missing syntax regex. Requests that mentioned later verbatim delivery were falsely classified as immediate exact-output; the orchestrator then removed every native tool schema and bypassed textual-protocol quarantine. Changed the invariant so all seven schemas are attached on the first and every exact-output provider request, while provider toolChoice=none separately prevents execution for a true literal response. Exact-output classification now requires an identifiable literal or sequence. Plain 'run a job' and 'start a new job' requests now force native run_job. Regression coverage uses both observed phone phrasings, verifies schemas on the first turn, quarantines run_job(...) before speech, and confirms structured Anthropic tool choice. Sidecar suite: 105 passing; TypeScript check and build pass.

**2026-07-27T04:43:08Z**

2026-07-26 physical-phone acceptance: three explicit Gettysburg background-job requests in sessions ses_d64f6e964d5540c826f673d2ac333efc, ses_7da030ca4c5e333a33eab20667659cfc, and ses_71a4b92e9ef5a9e9b784554f17058938 all produced native run_job reference envelopes and durable succeeded jobs. No textual/fenced tool imitation reached model speech. Operator confirmed the native routing behavior. Acceptance evidence is sufficient to close.
