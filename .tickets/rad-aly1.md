---
id: rad-aly1
status: closed
deps: []
links: []
created: 2026-07-17T23:28:49Z
type: bug
priority: 2
assignee: coopwilliams
parent: rad-7ur1
tags: [radcliffe, android, ui, dark-mode, accessibility]
---
# Honor Android system dark theme in the Radcliffe app

The Pixel is configured for system dark mode but Radcliffe continues to render a light theme, preventing the context indicator and conversation UI from being physically reviewed in dark mode.

## Acceptance Criteria

Radcliffe follows the Android system light or dark theme without requiring an app-specific toggle; all session-home, conversation, settings, message, provenance, context-indicator, error, and dialog surfaces use theme-aware colors; contrast meets accessibility requirements in both modes; system theme changes update safely during an active session and after process recreation; no hard-coded light surfaces remain; and screenshot or Compose coverage exercises both themes plus context warning, near-limit, unavailable, and overflow states.

## Notes

**2026-07-20T05:03:30Z**

Implemented system-following Compose and launch-window themes. The root now selects lightColorScheme/darkColorScheme from isSystemInDarkTheme, values-night removes the light launch/status/navigation bars, and every existing screen/message/dialog remains on Material semantic color roles. The unavailable context label moved from outline to onSurfaceVariant for text contrast. Host tests exercise both actual Compose schemes and assert WCAG 4.5:1 text / 3:1 context-arc contrast across app, settings, user/model/source/system/error, and normal/warning/near-limit/unavailable roles. Android unit/lint passes.
