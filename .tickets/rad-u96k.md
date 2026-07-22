---
id: rad-u96k
status: in_progress
deps: []
links: [rad-25m0, rad-e9wi, rad-rnt5]
created: 2026-07-22T14:23:06Z
type: feature
priority: 0
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, background-agent, claude-agent-sdk, anthropic, sandbox, operator-final-check]
---
# Replace custom background loop with Claude Agent SDK

Add a feature-flagged Claude Agent SDK executor that uses native WebSearch/WebFetch while preserving Radcliffe job lifecycle, cancellation, private transcripts, budget/runtime limits, credential isolation, sandboxed page downloads, and promoted artifacts.

## Design

Run the pinned Agent SDK in the trusted executor process with only WebSearch, WebFetch, and bounded in-process Radcliffe artifact tools. Keep Bash, host filesystem tools, subagents, skills, plugins, and user settings unavailable. Retain the existing Docker downloader and legacy custom loop as rollback. Configure model, effort, max turns, max spend, and prompt path locally.

## Acceptance Criteria

Deterministic tests cover tool isolation, local budget caps, path containment, exact source copying, and promoted results. A live bounded Sonnet spike completes the first-source Gettysburg commentary task within the configured budget, leaves no container/network behind, and the deployed phone workflow succeeds without dashboard-side configuration.



## Notes

**2026-07-22T14:25:08Z**

Live credentialed spike (2026-07-22): claude-sonnet-4-6, effort=low, max_turns=8, max_model_cost_usd=0.10 completed the first-readily-available Gettysburg commentary task in about 27 seconds / 5 turns / $0.06209075. Sequence: WebSearch, credential-free download_page, byte-exact copy_text_to_markdown, finish_job. Produced a 7,771-byte Markdown artifact identical to the saved Trafilatura extraction, with source and finding promotion; no Docker container or network remained. Local configuration lives in ignored var/job-executor.env and var/claude-agent-job-prompt.txt, so no Claude dashboard setup was needed. Awaiting operator phone verification before closure.
