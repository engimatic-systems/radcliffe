---
id: rad-qz6l
status: in_progress
deps: [rad-twr3]
links: []
created: 2026-07-17T20:10:03Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-b8nm
tags: [radcliffe, operations, backup, recovery, sqlite]
---
# Automate SQLite backup restore upgrade and rollback drills

Provide safe, versioned operational procedures and executable local drills for conversation storage and configuration recovery.

## Acceptance Criteria

One command creates a SQLite-consistent, permission-restricted backup plus redacted manifest without copying environment secrets; retention is bounded and dry-run/prune behavior is explicit; an isolated restore verifies integrity, schema versions, session/message/tool/playback/device-auth preservation, and current application startup; upgrade creates a preflight backup and records the deployed revision; rollback restores a selected compatible backup and service revision with confirmation; failure injection never replaces the live database; and tests exercise corruption, insufficient disk, interrupted copy, migration failure, and successful restore without paid providers.
