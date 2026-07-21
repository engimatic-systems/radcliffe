---
id: rad-ig6k
status: open
deps: []
links: [rad-uk3f]
created: 2026-07-21T15:19:35Z
type: bug
priority: 1
assignee: coopwilliams
tags: [radcliffe, voice, playback, barge-in, logs, operator-final-check]
---
# Treat stale playback acknowledgements after barge-in as harmless

The latest physical session logged repeated uncaught exceptions stating that nonterminal playback acknowledgements belonged to a stale generation after the user barged in. These late device acknowledgements are expected race residue and should not escape the LiveKit event handler.

## Design

Classify acknowledgements from superseded generations before applying them. Ignore or audit late nonterminal updates content-safely, while retaining terminal reconciliation and rejecting genuinely malformed current-generation acknowledgements.

## Acceptance Criteria

Delayed position/started acknowledgements after a barge-in do not throw, mutate current playback, or fail the event handler; telemetry records a stable stale-ack outcome; current-generation and terminal acknowledgement tests remain strict.


## Notes

**2026-07-21T15:33:18Z**

2026-07-21 worker logs for ses_103f1841319850c7a20dfcff234d84ef repeatedly raised nonterminal playback acknowledgement belongs to a stale generation immediately after barge-ins. This is independent of the job permission/retry fixes and remains open.

**2026-07-21T15:54:25Z**

2026-07-21 latest Pixel session reproduced this independently: after a confirmed barge-in at 08:46:33 local time, the worker logged one uncaught nonterminal playback acknowledgement belongs to a stale generation exception. Keep open.
