---
id: rad-c2tv
status: closed
deps: []
links: [rad-0ax4]
created: 2026-07-21T04:04:49Z
type: feature
priority: 3
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

## Notes

**2026-07-23T03:27:58Z**

Implementation complete on 1f76665. Automated evidence: make check passed (376 Python passed, 1 skipped; 107 sidecar tests; Android unit tests and lint; TypeScript, Ruff, and mypy). Review APK: /Users/ravd/agent-share/radcliffe/radcliffe-0.6.0-v10-1f76665-debug.apk, SHA-256 01275e294caeef214d2c904d3de718e866c1988de1349080342234984c721151. Leave in_progress for the acceptance criterion requiring a physical-phone TalkBack check of focus order, safe labels, exact-detail modal, and dismissal.

**2026-07-27T04:13:17Z**

PRs #59 and #60 are merged, and the operator confirmed personal verification of the TalkBack flow. The safe exact-detail affordance and review-build path are accepted; closing.
