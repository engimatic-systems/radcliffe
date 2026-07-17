---
id: rad-7hrk
status: open
deps: []
links: []
created: 2026-07-17T20:08:22Z
type: feature
priority: 3
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, android, export, html, ux, deferred]
---
# Export a conversation as self-contained HTML

Add an optional browser-friendly conversation export alongside the existing portable Markdown export without changing the current default.

## Acceptance Criteria

Android export offers HTML as an explicit format choice; the generated UTF-8 document is self-contained and readable offline, preserves chronological user/model/source and optional system entries with accessible high-contrast styling, escapes all conversation and provenance content so it cannot execute scripts or injected markup, applies the same credential/grant/host-path redaction policy as Markdown, uses Android's system document picker without broad storage permission, and tests cover empty, long, Unicode, source, hidden-system, redaction, and HTML-injection cases.
