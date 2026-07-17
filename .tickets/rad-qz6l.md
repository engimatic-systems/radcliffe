---
id: rad-qz6l
status: closed
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

## Notes

**2026-07-17T20:41:13Z**

Implemented in 804336f. Full gate passed (117 Python tests/1 skipped, 18 sidecar tests, TypeScript, Android unit/lint) plus standalone recovery-script mypy. Seven recovery tests cover online backup, exact session/envelope/tool/playback/device/auth preservation, current-app startup, secret scanning, corruption, insufficient disk, interrupted backup/restore, migration failure, bounded dry-run/apply retention, upgrade receipts, confirmed rollback, WAL/SHM retirement, and automatic database/revision/service recovery after injected failure. Live drill bkp_20260717T203904821604Z_6ca2b5ba and isolated restore preserved schema 6 and all durable row counts. Preflight bkp_20260717T204054867738Z_51e10fc5 verified and recorded deployed revision 804336f in a mode-0600 receipt. Operations remained fully ready; no cloud providers were called. Destructive live rollback was not invoked; its file mutation and recovery transaction ran only against isolated fixtures with fake revision/service controls.
