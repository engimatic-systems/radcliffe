---
id: rad-cnrg
status: open
deps: [rad-f08j, rad-mz5a]
links: [rad-f08j, rad-mz5a]
created: 2026-07-20T03:00:01Z
type: task
priority: 3
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, voice, prompt, tts, ux, research]
---
# Explore and prevent speech-hostile generated output

Generated conversational responses can contain identifiers and machine-oriented strings that are technically correct but laborious or confusing to hear, such as job IDs and hashes. The prompt and rendering policy need a broader inventory of these failure modes.

## Design

Build a representative spoken-output corpus covering hashes, UUIDs, job and message IDs, URLs, paths, timestamps, stack traces, serialized JSON, long numbers, code, citation machinery, repeated punctuation, and acronym-heavy metadata. Evaluate prompt instructions first, then bounded deterministic detection or alternate visual-only rendering where prompts are insufficient. Preserve exact canonical source reading and explicit user requests to spell or read machine text.

## Acceptance Criteria

The ticket documents failure categories and intended spoken alternatives; local and cloud prompt evaluations measure recurrence across representative turns; ordinary generated speech avoids unrequested hashes, IDs, raw URLs, paths, serialized objects, and other machine-oriented strings while the timeline retains useful visual detail; explicit read-it-exactly requests and canonical read_source rendering remain unchanged; accessibility review covers cases where visual-only substitution is inappropriate; and follow-up implementation tickets are created for any remedy too broad for one change.

