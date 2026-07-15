---
id: rad-c9h9
status: closed
deps: []
links: []
created: 2026-07-15T04:26:25Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, milestone-2, tools, security, pi, source-discovery]
---
# Discover authorized sources before deterministic reading

Add a metadata-only source catalog capability so the agent can identify an intended document from ordinary language before requesting its canonical bytes. This must reduce dependence on exact voice transcription without granting general filesystem access.

## Acceptance Criteria

A separately granted list_sources tool returns a stable, bounded, lexically sorted catalog of relative Markdown paths under configured readable roots and never returns file contents; symlinks outside roots, non-regular files, non-Markdown files, hidden paths, traversal, ambiguous roots, and over-limit enumeration are denied or excluded deterministically; every request, authorization decision, and result is idempotently audited while private absolute host paths stay out of model-visible output; Pi is instructed to call list_sources when the exact path is uncertain and then call read_source with one returned path; a faux-provider test proves the list-then-read sequence and proves canonical source bytes never enter model context; gateway/control tests cover capability separation, sorting, bounds, symlink escape, and replay; a local deployed smoke can request the demo source by description rather than dictated punctuation.


## Notes

**2026-07-15T04:34:12Z**

Implemented and locally deployed on 2026-07-14. A real Ollama turn for 'Read the demo file from my authorized sources. I do not know its exact filename.' executed list_sources followed by read_source and returned source:call_h0dzlp53 with no error. Gateway tests cover lexical sorting, entry/depth bounds, hidden/non-Markdown filtering, symlink escape, case ambiguity, capability separation, private relative output, audit replay, and idempotency. Full repository gate passed.
