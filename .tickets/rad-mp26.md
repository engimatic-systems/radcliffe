---
id: rad-mp26
status: in_progress
deps: [rad-tx88, rad-lod5, rad-yk82]
links: [rad-lod5, rad-yk82, rad-h2lv]
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


## Notes

**2026-07-14T03:35:26Z**

Automatic random sessions remain deferred: the current local media worker is pinned to radcliffe-demo, so generating a new room in the Android client would create a room without an agent. The persisted-profile APK retains radcliffe-demo as a saved compatibility setting until the durable server catalog and worker lifecycle in rad-lod5 are available.

**2026-07-14T11:53:12Z**

Backend prerequisites are now implemented: rad-lod5 provides the durable generated-ID/recent-session API and rad-yk82 dispatches exactly one managed media worker for generated rooms. Android can now replace the compatibility session field with automatic create/reopen behavior once the profile PR chain lands.

**2026-07-14T12:35:29Z**

Implemented on feat/android-session-home: configuration-only setup, generated sessions with persisted idempotency, authenticated recent-session paging, chronological timeline restore before LiveKit connect, selected-session persistence, diagnostic-only raw IDs, explicit loading/empty/offline/unauthorized/retry states, and archived-test filtering. JVM/API tests cover creation-key recreation, parsing, reducer pagination/order/deduplication, failures, and timeline secret/audit filtering. Keep open for the Pixel UI/process/reboot checkpoint.
