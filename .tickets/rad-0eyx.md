---
id: rad-0eyx
status: closed
deps: []
links: [rad-xhlm]
created: 2026-07-19T03:24:11Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-nhai
tags: [radcliffe, android, jobs, voice, events]
---
# Show and control background jobs on Android

Render session-scoped background job lifecycle on Android and let an enrolled user issue bounded follow-up, priority, and cancel commands without interrupting foreground voice.

## Design

Hydrate a privacy-minimized job summary list through the device API, fold replayed Protocol v2 JobEvent messages into a session-scoped model by delivery sequence and job revision, and present accessible status cards in the active conversation. Commands use the enrolled device credential and existing idempotent job command endpoint; raw job instructions and executor output are never loaded or displayed.

## Acceptance Criteria

Android accepts only schema-valid JobEvent records for the active session, deduplicates reconnect replay, ignores stale revisions and wrong-session events, and clears jobs on handoff; current job summaries survive activity recreation through the process model and refresh from the device API; active jobs expose bounded follow-up, priority, and cancel controls with in-flight/error state; terminal jobs reject invalid controls; UI and parser tests cover waiting, completion, failure, simultaneous jobs, replay, session switch, and privacy.


## Notes

**2026-07-19T03:36:40Z**

Implemented the active-session Android job model, enrolled-device hydration and idempotent controls, Protocol v2 replay/deduplication, bounded lifecycle cards, accessibility state, privacy guards, and session-handoff isolation. Repository-wide make check passes: 276 Python passed (1 skipped), 41 sidecar tests passed, and Android unit/lint passed including 8 background-job tests.
