---
id: rad-nosv
status: open
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
