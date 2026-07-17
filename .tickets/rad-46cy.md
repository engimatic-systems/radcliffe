---
id: rad-46cy
status: closed
deps: []
links: []
created: 2026-07-17T03:04:19Z
type: feature
priority: 2
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, android, export, ux, apk-batch]
---
# Export a conversation as portable Markdown

Export the visible durable conversation through Android's system document picker as a human-readable Markdown file without granting broad storage access.

## Acceptance Criteria

From a conversation, the user can create a UTF-8 .md file with a safe deterministic filename; export restores the complete paginated durable timeline in chronological order and labels user, model, authorized source, and optionally system entries; Markdown-sensitive content is preserved without becoming executable HTML; source provenance and session date are included while credentials, raw grants, internal audit metadata, and host paths are excluded; cancellation leaves no app-side partial state; tests cover empty, long/paginated, Unicode, source, hidden-system, and redaction cases.

## Notes

**2026-07-17T03:27:30Z**

Implemented UTF-8 Markdown export through Android's CreateDocument system picker. Export refetches the complete paginated durable timeline, orders by sequence, labels provenance and timestamps, uses collision-safe code fences, strips host path prefixes, and redacts device/provider/bearer/grant/audit metadata. Tests cover empty, Unicode, Markdown/HTML, source paths, hidden/included system messages, deterministic filenames, and redaction.
