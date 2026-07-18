---
id: rad-fxgg
status: open
deps: [rad-nhwy]
links: []
created: 2026-07-18T02:30:45Z
type: feature
priority: 3
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, android, ui, system-messages, accessibility]
---
# Compact the system-message toggle into an accessible icon

Replace the space-consuming Show system messages text control in the conversation view with a compact square icon that visibly reflects whether system messages are shown.

## Design

Use a small square visual inside a standard Android minimum touch target. Render the off state in grayscale and the on state in orange, with outline/filled or another non-color selected cue and semantic toggle state so accessibility does not depend on color.

## Acceptance Criteria

The conversation toolbar uses a compact square icon instead of the visible Show system messages label; the off state is grayscale and the on state is orange; selected state is also distinguishable without color; the visual may be small but the touch target meets Android accessibility sizing and does not crowd adjacent controls; TalkBack announces Show system messages or Hide system messages plus checked state; tapping preserves the existing immediate filtering, ordering, persistence, diagnostics, and export behavior; focus, large-font, narrow-screen, light/dark-theme, and rapid-toggle tests pass; and no tooltip or transient label obscures the active conversation.

