---
id: rad-1w52
status: closed
deps: []
links: [rad-c6wp, rad-au20, rad-2e0w, rad-ihso, rad-l386, rad-lezo, rad-orob, rad-wobn]
created: 2026-07-25T15:19:17Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, voice, source, playback, context, barge-in, operator-final-check]
---
# Persist only client-heard source prefixes in conversation context

Canonical source playback currently places the entire document in foreground Haiku context before the operator has heard it. This exhausts session context and lets the model know text that was never played.

## Design

Treat read_source as playback authorization, not document injection. Build the conversation source envelope only from device-acknowledged playback offsets. Persist acknowledged words or short phrase prefixes continuously without blocking audio; on interruption, flush the acknowledged prefix before projecting the interrupting user turn. Never project the unheard suffix. Preserve deterministic source playback and terminal postamble suppression.

## Acceptance Criteria

A long source can begin playback without its full text entering the model context; completed acknowledgements project the heard prefix; a mid-sentence interruption projects every acknowledged word through the high-water mark before the user message and no unheard suffix; stale or duplicate acknowledgements remain safe; model postambles stay suppressed; fake-client and service tests cover complete, interrupted, and resumed playback.


## Notes

**2026-07-25T15:36:40Z**

2026-07-25 implementation is locally green. ToolGateway now keeps the full canonical source only in the non-conversational tool audit/renderer handoff. The media worker appends one TEXT_SOURCE envelope per completed sentence and maps a device FLUSHED high-water mark to a conservative word boundary; cancellation waits up to 500 ms for the terminal device acknowledgement and commits the acknowledged prefix before the next USER envelope. Regression coverage proves a half-sentence prefix reaches the next context packet while the unheard suffix does not.

**2026-07-27T04:13:17Z**

PR #63 is merged. The integrated pipeline keeps full canonical source text out of conversational context and persists only device-acknowledged heard prefixes, including interruption handling. The operator authorized closure after merged-chain verification.
