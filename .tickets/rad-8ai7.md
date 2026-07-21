---
id: rad-8ai7
status: in_progress
deps: []
links: [rad-uk3f, rad-l386]
created: 2026-07-21T14:35:08Z
type: task
priority: 0
assignee: coopwilliams
tags: [radcliffe, haiku, qwen, models, user-testing, operations, operator-autonomous]
---
# Require Haiku for Radcliffe user-test profiles

Foreground phone testing switched to Haiku, but the background Unix job agent remained configured for local Qwen and exhausted its step limit on a simple Hacker News extraction. User acceptance runs can therefore silently mix a frontier foreground model with a weak local job model.

## Design

Pin Haiku for every model role used by operator/user acceptance: foreground sidecar and background job broker. Add an explicit managed user-test activation path and a content-free preflight that rejects Qwen or any unpinned provider/model. Retain the Qwen implementation only as an explicitly selected deprecated development stub; it must never be the default or accepted evidence for a user test. Keep provider credentials in the trusted host broker and out of disposable command containers.

## Acceptance Criteria

One explicit command installs/restarts the managed Haiku user-test profile; phone preflight proves the foreground and background model roles are pinned to claude-haiku-4-5-20251001; the background host broker may receive only the explicitly approved Anthropic credential while model-selected containers receive none; ordinary local/deterministic tests make no paid calls; Qwen remains available only behind a clearly deprecated explicit development selector and cannot satisfy user-test preflight; operations documentation and AGENTS instructions state that future user tests use Haiku.


## Notes

**2026-07-21T14:35:25Z**

2026-07-21 mixed-model root cause: sidecar health correctly reported Anthropic Haiku, but var/job-executor.env still launched sandbox_agent with --model qwen3:8b through Ollama. The Hacker News job repeated obsolete HTML parsing, malformed API commands, eight empty assistant actions, and identical timeouts before the 24-step resource limit. Future user-test preflight must cover both foreground and job model roles.
