---
id: rad-nhwy
status: in_progress
deps: []
links: []
created: 2026-07-17T03:04:19Z
type: feature
priority: 2
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, android, messages, ux, apk-batch]
---
# Toggle system messages in the Android conversation view

Let the user hide protocol/system messages from the conversational transcript while retaining them for diagnostics and durable audit.

## Acceptance Criteria

Conversation settings expose an accessible Show system messages toggle; the default prioritizes user, model, and authorized-source content; toggling updates the current timeline immediately and persists locally across relaunches; hiding never deletes, mutates, or excludes system envelopes from diagnostics/export when explicitly requested; user/model/source cards and live incoming messages retain stable ordering; Compose reducer tests cover restored and live timelines in both modes.
