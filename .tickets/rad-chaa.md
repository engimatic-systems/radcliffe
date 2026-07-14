---
id: rad-chaa
status: closed
deps: []
links: [rad-rwfl, rad-lo0t, rad-214n, rad-ghti, rad-nu8n]
created: 2026-07-14T04:05:29Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-3se2
tags: [voice, latency, streaming]
---
# Bound first-audio latency for punctuation-poor streamed output

The local voice loop waits for sentence punctuation before synthesizing. Markdown, emoji grids, and other punctuation-poor responses can therefore delay first audio until most or all generation completes; the Pixel run measured about 13 seconds for an emoji mandala.

## Design

Flush model speech on sentence boundaries plus deterministic maximum character and completed-line boundaries while preserving text order. Record phase timings needed to separate endpointing, STT, generation, synthesis, and publish latency. Exact text-source reading must retain its independent lossless rendering path.

## Acceptance Criteria

Markdown bullets, headings, emoji grids, and long punctuation-free output begin synthesis before the entire response completes; chunk concatenation preserves canonical model text and order; segment identities and boundary indexes remain unique across streaming deltas; phase-level latency evidence is emitted without message content or secrets; exact source-reading behavior is unchanged; automated regressions cover the emoji-mandala shape and five local turns meet the documented target or retain actionable phase evidence.

## Notes

**2026-07-14T04:13:51Z**

Implemented on fix/voice-output-hardening: streaming speech now flushes at sentence, completed-line, and configurable maximum-character boundaries with unique monotonic segment IDs. Endpoint, transcription, first-delta, synthesis, publish, and first-audio timings are content-free and actionable. Local LiveKit smoke passed at 2.809s first audio and 53ms interrupt acknowledgement.
