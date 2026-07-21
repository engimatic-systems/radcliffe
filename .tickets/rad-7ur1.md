---
id: rad-7ur1
status: in_progress
deps: [rad-mp26, rad-4myy, rad-aly1]
links: [rad-e0qf]
created: 2026-07-14T02:48:09Z
type: feature
priority: 2
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, milestone-2, android, context, ui, operator-final-check]
---
# Render accessible context-usage donuts in the Android session list

Add the compact circular context indicator proposed for each recent-session row.

## Acceptance Criteria

Each session row renders a circular usage indicator from server-provided context accounting; the indicator distinguishes normal, warning, near-limit, unknown, and unavailable states without relying on color alone; normal rows do not display the redundant phrases `Context normal` or `Exact count`, while exact-versus-estimated status remains available to accessibility or details surfaces; accessibility text announces used and remaining percentages plus whether the count is estimated; dates and usage refresh without reordering glitches; large font, dark mode, TalkBack, reduced motion, and narrow-screen layouts pass; boundary values at 0, warning threshold, 100, and over-limit are tested; and the component never derives a misleading percentage when model limit or accounting basis is unavailable.


## Notes

**2026-07-14T12:35:29Z**

Implemented accessible recent-session context indicators using exact server accounting: normal/warning/near-limit/overflow plus loading/unavailable states, visible non-color glyphs, TalkBack used/remaining/exact-or-estimated labels, progress semantics, dark-mode Material colors, no animation, and narrow two-line rows. Boundary/reducer JVM tests pass. Keep open for physical large-font/dark-mode/TalkBack inspection on the Pixel.

**2026-07-15T03:15:48Z**

2026-07-14 Pixel v0.3.2 session-home functionality was accepted during new/reopened/switched conversation testing. Dedicated TalkBack, large-font, and dark-mode inspection remains outstanding.

**2026-07-17T23:28:54Z**

2026-07-17 Pixel review: context donuts appeared normal and sessions/configuration survived reboot. The Pixel was already in system dark mode, but Radcliffe still rendered a light theme; tracked as blocking bug rad-aly1. Large-font and TalkBack inspection was not performed because those Android system accessibility controls were mistaken for in-app settings; keep those physical gates open.

**2026-07-18T02:30:35Z**

UI density refinement: remove the visible phrases 'Context normal' and 'Exact count' from recent-session rows. Keep exact-versus-estimated status in accessibility semantics or a details surface, keep exceptional warning/near-limit/unavailable/overflow states understandable without color alone, and let the donut carry the normal state visually.
