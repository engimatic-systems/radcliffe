---
id: rad-yk82
status: open
deps: []
links: [rad-lod5, rad-mp26, rad-h2lv]
created: 2026-07-14T05:36:40Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, milestone-2, workers, sessions, orchestration]
---
# Dispatch media workers for server-generated sessions

Generated session IDs are not usable from Android while the media worker is pinned to one launch-time room. Add a local worker supervisor so create/reopen can attach the voice pipeline to the requested room without manual token or process setup.

## Design

Keep worker lifecycle behind a control-plane dispatch interface. The local implementation may supervise one isolated worker task/process per active session, keyed idempotently by session ID and generation; the eventual remote edge can replace the dispatcher without changing the session API.

## Acceptance Criteria

Creating or reopening a generated session causes exactly one authorized worker participant to join the room; concurrent/retried dispatch is idempotent; the worker uses a short-lived worker-role token without exposing it to Android; end and bounded idle expiry stop orphaned work; supervisor restart reconciles active sessions safely; startup and failure state are visible without leaking secrets; existing compatibility workers remain supported during migration; and local integration tests cover dispatch races, worker failure, cleanup, and restart.
