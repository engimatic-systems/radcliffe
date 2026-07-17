---
id: rad-aly1
status: open
deps: []
links: []
created: 2026-07-17T23:28:49Z
type: bug
priority: 1
assignee: coopwilliams
parent: rad-7ur1
tags: [radcliffe, android, ui, dark-mode, accessibility]
---
# Honor Android system dark theme in the Radcliffe app

The Pixel is configured for system dark mode but Radcliffe continues to render a light theme, preventing the context indicator and conversation UI from being physically reviewed in dark mode.

## Acceptance Criteria

Radcliffe follows the Android system light or dark theme without requiring an app-specific toggle; all session-home, conversation, settings, message, provenance, context-indicator, error, and dialog surfaces use theme-aware colors; contrast meets accessibility requirements in both modes; system theme changes update safely during an active session and after process recreation; no hard-coded light surfaces remain; and screenshot or Compose coverage exercises both themes plus context warning, near-limit, unavailable, and overflow states.

