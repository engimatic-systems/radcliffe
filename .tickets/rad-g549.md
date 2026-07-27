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
