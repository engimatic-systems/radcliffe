---
id: rad-25m0
status: in_progress
deps: []
links: []
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
