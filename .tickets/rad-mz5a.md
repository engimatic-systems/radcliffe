---
id: rad-mz5a
status: in_progress
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

## Notes

**2026-07-20T04:53:31Z**

Implemented the identity boundary in both model-facing prompts: each identifies Radcliffe once as the harness and identifies the selected model as operating inside it; neither assigns or asks for a Radcliffe persona. Local/foreground and background prompt snapshots reject You are/act as/impersonate Radcliffe while retaining existing tool and safety assertions. Focused verification: 55 sidecar tests and 10 sandbox-agent tests pass. Deployed local qwen3:8b answered the identity probe as an AI assistant and described Radcliffe as the surrounding system, not itself. The actual paid Haiku probe remains intentionally unrun because repository policy requires separate explicit paid-smoke approval; no cloud credential was exposed or used.
