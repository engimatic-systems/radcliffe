---
id: rad-mp26
status: open
deps: [rad-tx88, rad-lod5]
links: []
created: 2026-07-14T02:48:09Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, milestone-2, android, sessions, compose]
---
# Build the Android recent-session home and automatic session creation

Replace the manually typed session field with a home screen that creates sessions automatically and reopens recent conversations.

## Acceptance Criteria

After configuration, app launch shows recent sessions ordered by server last activity descending; each row shows a stable title or fallback label and human-readable date; New session requests a server-generated ID and starts without exposing the ID as required input; selecting a recent session obtains a fresh room token and restores its persisted message timeline before connecting; returning from a session refreshes recency; loading, empty, offline, expired-auth, pagination, and retry states are accessible and tested; process recreation retains the selected session without duplicating it; and an advanced diagnostics view may expose the raw session ID without making it normal UI.

