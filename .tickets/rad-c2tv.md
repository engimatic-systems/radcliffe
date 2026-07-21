---
id: rad-c2tv
status: open
deps: []
links: []
created: 2026-07-21T04:04:49Z
type: feature
priority: 2
assignee: coopwilliams
parent: rad-cnrg
tags: [android, accessibility, spoken-output, operator-final-check]
---
# Add an accessible machine-detail timeline affordance

Spoken alternatives tell the user that technical detail is available in the conversation, but Android has no dedicated accessible indicator or action for reviewing that detail. Add a TalkBack-friendly affordance without automatically reading hostile machine strings.

## Design

Mark messages whose speech differed from display, expose a concise accessible label, and offer an explicit user action to read or spell the original detail. Treat screen-reader-only and voice-only operation as first-class cases.

## Acceptance Criteria

TalkBack announces that visual technical detail is available without reading raw identifiers by default; the user can explicitly request exact reading; ordinary touch and voice flows are unchanged; a physical-phone TalkBack check verifies focus order, labels, and exact-reading escape hatch.
