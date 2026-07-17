---
id: rad-nosv
status: closed
deps: []
links: []
created: 2026-07-17T03:04:19Z
type: feature
priority: 2
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, android, sessions, ux, apk-batch]
---
# Archive conversations from the Android session home

Add an ordinary Android action for removing finished conversations from the default recent list without deleting their durable messages or audit history.

## Acceptance Criteria

A user can archive the current conversation or a recent-session row with an explicit confirmation; the app calls the authenticated status-only session update without erasing its title; archived sessions disappear from the default reverse-chronological list, cannot be archived twice accidentally, and remain recoverable through a clearly separated archived view or documented API path; offline, unauthorized, and retry outcomes are visible; unit/API tests cover confirmation, idempotency, refresh ordering, and preservation of messages.

## Notes

**2026-07-17T03:27:30Z**

Implemented confirmed archive actions for recent rows and the live conversation, an authenticated archived-only catalog filter, a separated Archived view with Restore, mutation guards, and visible failure states. API tests prove title/message preservation, idempotent retry, and restore. Live worker smoke artifacts/worker-dispatch/20260717T032656Z/report.json passed archive hidden/recoverable plus worker cleanup.
