---
id: rad-mz5a
status: open
deps: []
links: [rad-e5na, rad-cnrg]
created: 2026-07-20T01:53:18Z
type: bug
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, prompt, identity, models, anthropic, ollama]
---
# Describe Radcliffe as the harness without assigning the model its identity

The model-facing system prompt currently says You are Radcliffe, causing the selected model to claim a false Radcliffe identity even though Radcliffe is the harness.

## Design

Remove instructions that assign Radcliffe as the model persona from every model-facing prompt. State once that the harness is called Radcliffe.

## Acceptance Criteria

No foreground or background model prompt tells a model that it is Radcliffe or asks it to impersonate Radcliffe; the harness is identified in one plain sentence as Radcliffe; a Haiku session no longer introduces itself as Radcliffe; local and cloud prompt snapshots enforce the identity boundary while all tool, safety, no-emoji, and source-integrity behavior remains intact.
