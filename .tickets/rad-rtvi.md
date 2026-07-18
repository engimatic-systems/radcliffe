---
id: rad-rtvi
status: open
deps: [rad-7h1e]
links: []
created: 2026-07-17T23:28:43Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-1wji
tags: [radcliffe, agent-control, sessions, android]
---
# Let the agent create switch and restart conversations

Let a spoken request create a fresh conversation, switch to an authorized recent conversation, or restart the current interaction into a new server-owned session without manual navigation.

## Design

Use explicit idempotent session actions rather than restarting the Android process. Resolve named recent sessions against the authenticated catalog, require confirmation where ambiguity or destructive effects exist, stop current media ownership, and hand Android a durable transition result.

## Acceptance Criteria

The active agent can request new, switch, end, and restart-into-fresh-session actions only for the enrolled principal; a fresh session receives a server-generated ID and exactly one worker; ambiguous references produce a bounded choice or clarification rather than guessing; history is preserved and no content is copied unless a separately authorized action says so; the originating generation cannot emit speech or actions after handoff; repeated or reconnected requests are idempotent; Android visibly transitions or reports failure and retains a safe selected session across process recreation; no worker, room token, or playback remains orphaned; and local conformance covers create, catalog resolution, switch, end, replay, cancellation race, worker failure, and control-plane restart.

