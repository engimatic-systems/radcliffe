---
id: rad-20nk
status: closed
deps: []
links: [rad-16wv, rad-t0va, rad-cnrg, rad-0ch6]
created: 2026-07-20T19:47:37Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-1wji
tags: [voice, configuration, tools, speech-rate, p0, operator-autonomous]
---
# Route spoken delivery-speed requests without malformed or unrelated tool calls

A spoken request to 'Count slowly from 1 to 60' produced a visible configure tool failure, retried with a persistent session-scoped default_speech_rate mutation, then invoked read_source even though no source was requested; that second tool also failed. Button-driven rate changes immediately before this turn all succeeded. Telemetry proves the two failures occurred in local tool preparation/validation before sidecar execution, while the retry reached authorization and completed. The exact malformed model arguments were not retained, so the precise invalid field/value is indeterminate. Current prompt and schemas expose conflicting rate representations: configure accepts 0.75x-style strings, read_source accepts slow-style aliases or numbers, and prompt text can be read as applying read_source to any explicit delivery-speed request.

## Design

Define an unambiguous intent-to-tool contract for one-turn delivery style versus session configuration versus reading a named source. Give compatible canonical speed representations or deterministic normalization at the tool boundary. A transient utterance modifier such as 'count slowly' must not mutate session defaults unless persistence is explicit, and must never select read_source without a source-reading intent and path. Retain validated rejected tool-call details in secure diagnostic telemetry, while keeping user-facing recovery grounded and concise. Repair the malformed prompt adjacency around job/source guidance and add cross-profile tool-routing conformance cases.

## Acceptance Criteria

1. 'Count slowly from 1 to 60' is handled as a one-turn delivery instruction and produces no configure or read_source failure. 2. The request does not change the session default_speech_rate unless the user explicitly asks to make it a session/default setting. 3. read_source is never invoked without explicit source-reading intent and a valid path. 4. Persistent requests such as 'speak at 0.75x for this session' perform exactly one valid configure mutation. 5. Local and Haiku profiles pass the same routing corpus for slowly, faster, this answer, this session, default, and named-source variants. 6. Rejected tool calls retain redacted structured validation diagnostics sufficient to identify the invalid field/value. 7. The user does not receive a generic 'Tool call failed' envelope for a recoverable model-generated schema mismatch.


## Notes

**2026-07-20T19:48:12Z**

Observed 2026-07-20 in trace trc_23a69875d0d3405ba9740cabd6e75ba5: configure failed locally at 17:12:33.840 with no sidecar/control execution; retry authorized/completed at 17:12:34.878-34.880 and persisted default_speech_rate=0.75x at session scope; read_source then failed locally at 17:12:36.076 with no sidecar execution. Exact rejected arguments were not retained and must not be claimed. All Android button-driven mutations immediately before the utterance succeeded.

**2026-07-21T03:52:22Z**

Implemented deterministic delivery intent routing: transient generated responses apply exactly one turn-scoped canonical rate, persistent wording applies exactly one session-scoped mutation, and named-source requests retain source-tool routing. Added canonical rate normalization, repaired source/job prompt adjacency, and redacted structured invalid-argument diagnostics; recoverable schema mismatches no longer persist the generic Tool call failed message. Local tests: 64 sidecar tests and 33 pipeline tests pass. Real-profile content-free corpus passed all 6 cases on qwen3:8b (3492 input / 152 output tokens) and pinned claude-haiku-4-5-20251001 (13024 input / 196 output tokens, estimated cost USD 0.014004). Reports: artifacts/delivery-routing/qwen-report.json and artifacts/delivery-routing/haiku-report.json. Both temporary sessions ended and test devices revoked.
