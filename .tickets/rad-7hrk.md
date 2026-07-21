---
id: rad-7hrk
status: closed
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

## Notes

**2026-07-20T05:06:32Z**

Implemented explicit Markdown-default/HTML format selection through separate Android system document-picker MIME contracts. HTML rendering is UTF-8, chronological, semantic, offline/self-contained, responsive, light/dark high-contrast, and protected by a restrictive CSP. Title, session metadata, provenance, and all message text are redacted then HTML-escaped. Tests cover empty documents, 5,000-character content, Unicode, ordered user/model/source entries, hidden and explicitly included system entries, credentials, bearer/assignment/host-path redaction, and injected title/img/svg/script markup. Android unit/lint passes.
