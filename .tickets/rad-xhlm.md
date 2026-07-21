---
id: rad-xhlm
status: open
deps: []
links: [rad-0eyx, rad-v2mj]
created: 2026-07-20T01:53:18Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-1wji
tags: [radcliffe, android, ui, configuration, jobs, accessibility]
---
# Keep the Android conversation timeline usable with compact controls

The active conversation screen gives substantial fixed vertical space to the Conversation behavior card and background-job controls, leaving the message timeline too small to use once jobs appear.

## Design

Remove the Conversation behavior card and replace it with one compact cycling button for the registered default speech rate and one for response detail. Each tap advances deterministically and immediately displays the new effective value; render brief, standard, and detailed as +, ++, and +++. Preserve mutation progress, failure feedback, effective-state reconciliation, and accessibility. Bound or collapse job controls so they cannot starve the message timeline; select the exact multi-job presentation after the product clarification.

## Acceptance Criteria

The large Conversation behavior box is gone; two compact accessible buttons show and cycle the effective speech rate and detail level, with detail rendered as +, ++, or +++; pending, success, reconnect, and failure states cannot display a value that was not applied; adding one or several jobs leaves a practically usable conversation timeline on the target Pixel; job status and controls remain reachable; Compose/UI tests cover all cycles, restored state, errors, multiple jobs, font scaling, and TalkBack labels.

## Notes

**2026-07-20T02:12:02Z**

2026-07-19 product clarification: background jobs use an expandable summary; the speech-rate control displays exactly one current value at a time, and each tap advances to the next value in the registered cycle.
