---
id: rad-0jby
status: closed
deps: []
links: []
created: 2026-07-15T04:26:25Z
type: bug
priority: 1
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, milestone-2, android, accessibility, provenance]
---
# Render high-contrast source and provenance cards

The source provenance label in the Pixel conversation timeline has insufficient contrast. Give canonical source messages an intentionally distinct Material 3 container and matching content colors instead of coloring only the tiny label.

## Acceptance Criteria

The complete source card uses a paired Material 3 container/on-container color treatment in light and dark themes; source identity and provenance are presented as readable text with a clear source label; user, model, and system cards retain semantically paired colors; implementation exposes semantics suitable for TalkBack; Android unit/lint gates pass and an updated versioned APK is staged in the Radcliffe share folder.


## Notes

**2026-07-15T04:34:12Z**

Implemented paired Material 3 container/on-container colors for every conversation card, with the full source card using tertiaryContainer/onTertiaryContainer and a merged TalkBack-readable 'Source · filename' heading. Android unit tests and lint pass. Version 0.3.3 APK and checksum are staged under /Users/ravd/agent-share/radcliffe.
