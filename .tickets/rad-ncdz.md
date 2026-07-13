---
id: rad-ncdz
status: open
deps: [rad-l1jc]
links: []
created: 2026-07-13T01:52:53Z
type: feature
priority: 0
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, milestone-1, demo]
---
# Milestone 1: demo Android voice and deterministic source reading

Integrate the foundation components into the single verifiable Pixel demo that gates the first major PR.

## Acceptance Criteria

From a clean documented setup, the target Pixel connects to the self-hosted LiveKit topology; an ordinary spoken question yields a visible transcript and spoken frontier-model answer within five seconds; speech or tap interruption stops audible output within 500 ms and accepts the next turn; asking to read the supplied Markdown fixture causes Pi to request read_source and the Tool Gateway to send canonical source text directly to TTS with visible provenance; app and system controls pause and resume at the persisted sentence boundary; ending and restarting leaves no orphaned operation; the independent suite produces a passing report with audio, latency, envelope/state timeline, canonical-text proof, and tool audit; and the README contains a concise demo script.
