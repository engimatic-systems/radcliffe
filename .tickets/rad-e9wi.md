---
id: rad-e9wi
status: in_progress
deps: []
links: [rad-25m0, rad-rnt5, rad-u96k, rad-orob]
created: 2026-07-22T02:55:49Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, background-agent, artifacts, provenance, haiku, operator-final-check]
---
# Honor exact artifact scope and verbatim provenance in background jobs

A Haiku background job asked for a complete verbatim Gettysburg Address but generated the text from model memory, appended an unrequested Historical Note, labeled the output as the Nicolay copy without source evidence, and cited the generated artifact as its own source.

## Design

Tell the job model to match requested artifact content, format, and scope exactly; forbid unrequested commentary and metadata; require complete/verbatim text to come from a saved source and be checked against it; forbid memory reconstruction and self-citation as evidence; fail with source_unavailable when provenance cannot be verified.

## Acceptance Criteria

Regression assertions cover the artifact-scope and provenance policy; a real Haiku job obtains a saved source before producing verbatim output and does not append unrequested sections; the next operator phone test confirms the requested result file contains only the requested content.


## Notes

**2026-07-22T02:56:01Z**

Root cause evidence from job_f28b26ea8773e7586d72914f84e61dcd: the saved HTML was never exposed to Haiku because download_and_preview_page returned failure. Haiku's own run_shell Python payload explicitly authored the '## Historical Note' section and its two paragraphs, then falsely summarized the file as complete/verbatim and based on the Nicolay copy. The promoted result cited gettysburg_address.md itself rather than an independently downloaded canonical source.

**2026-07-22T03:00:26Z**

A controlled Haiku 4.5 run with the repaired large-page preview sourced the speech from the saved HISTORY extraction, wrote an artifact with no title, metadata, preface, Historical Note, commentary, or conclusion, and ran an exact source comparison successfully. The deliberately tight /bin/zsh.06 smoke cap stopped the run after a redundant final read at /bin/zsh.068884, before finish_job; production remains capped at /bin/zsh.25. Added bounded verification guidance and improved preview ranking to surface the full-text passage directly. Keep open for the next operator phone check.
