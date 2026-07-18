---
id: rad-3se2
status: open
deps: []
links: []
created: 2026-07-14T02:46:46Z
type: epic
priority: 1
assignee: coopwilliams
tags: [radcliffe, milestone-2, sessions, android, voice]
---
# Milestone 2: durable sessions and deterministic conversation control

Turn the successful single-room foundation demo into a durable daily-use experience with deterministic interruption semantics, saved local configuration, server-owned session history, resumable recent sessions, and visible context-budget accounting.

## Acceptance Criteria

Interrupted or superseded turns have an explicit persisted disposition and cannot bleed into a later prompt; only one voice/model operation can own a session generation at a time; the Android app securely remembers its local connection profile; new session identifiers are generated automatically; recent sessions are listed newest-first and can be reopened; each session reports deterministic context usage against the configured model limit; Android renders an accessible context-usage indicator; and an external Oxford-derived suite covers cancellation races, endpoint splits, process restart, catalog ordering, session resume, and budget accounting.


## Notes

**2026-07-14T03:03:23Z**

Cloud-provider testing policy: funded OpenAI and Anthropic credentials remain disabled by default. Tests use fakes or the local Ollama/Whisper/TTS profile unless the user explicitly authorizes a bounded run and RADCLIFFE_ALLOW_CLOUD_MODELS=1 is set for that process.
