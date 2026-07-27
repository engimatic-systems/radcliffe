---
id: rad-g549
status: in_progress
deps: []
links: []
created: 2026-07-27T15:13:25Z
type: bug
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, milestone-1, sessions, voice, regression]
---
# Allow voice-created conversations after turn-generation advances

The final M1 Pixel run in session ses_9e9b88dffe96419826e8e03a4e75a1fc passed buffering, jobs, latency, interruption, exact source, and resume, but every manage_session(create) call returned HTTP 409 after the voice engine had advanced to operation generation 11 while the durable session catalog remained at lifecycle generation 1. The control plane incorrectly compares these distinct generation domains before committing the transition.

## Acceptance Criteria

A manage_session create request remains authorized after multiple voice interruptions/reconnects have advanced the operation generation beyond the durable catalog generation; the transition atomically ends the source, creates exactly one managed destination, and replays idempotently. Regression coverage proves operation generation greater than one and replay behavior. The pinned Sonnet 5 Pixel can create a new conversation by voice after a multi-turn session, with no 409 and no orphaned worker.


## Notes

**2026-07-27T15:14:30Z**

Root cause and local fix, 2026-07-27: SessionEngine.generation is an operation-supersession counter and had advanced to 11 after the test's interruptions/reconnect. sessions.generation is a durable lifecycle version and remained 1. _apply_session_transition incorrectly required equality, producing all three HTTP 409s before any session_transitions row. The fix retains operation-staleness authorization at the Tool Gateway/AgentControl boundary, then uses the catalog record's generation for the atomic active-source transition check. Completed-action replay likewise no longer compares the two domains. A regression creates at operation generation 11, replays at 12, and verifies the durable transition records source generation 1.

**2026-07-27T15:32:49Z**

2026-07-27 Pixel retest: both manage_session(create) calls committed successfully. Resumed source ses_9e9b88dffe96419826e8e03a4e75a1fc created ses_24e3274115b089d5e6b16c642281f2c5 at operation generation 3, and fresh source ses_45378fc04dabe3753f8fab35a3c340ea created ses_7712e8f468e13881fd30475d05d215c1 at generation 2; the fresh destination had no prior memory. The apparent repeat failure in the resumed session was old failure context, not another rejected tool call. A separate handoff race was exposed: the first commit landed 1.1s before the next 10s worker reconciliation boundary, which SIGTERM'd the source while the successful sidecar result was still in flight, before the session_transition/handed_off envelopes could reach Android. Added a configurable 5s transition-delivery grace to the supervisor plus deterministic regression. Full make check passes: 464 Python passed/1 skipped, 112 sidecar tests, Android unit/lint.
