---
id: rad-c6wp
status: closed
deps: []
links: []
created: 2026-07-17T00:45:17Z
type: bug
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, voice, source, determinism]
---
# Suppress model postambles after deterministic source reads

A live Ollama turn emitted a model refusal after Radcliffe had already completed a canonical read_source rendering. Treat a successful canonical source handoff as the terminal assistant output for that turn.

## Acceptance Criteria

After a successful read_source result, later model_delta and model_complete events cannot produce MODEL envelopes, UI messages, context entries, or TTS; the canonical TEXT_SOURCE envelope and tool audit remain unchanged; pre-source rendered model output remains auditable; fake-sidecar and external conformance cover a provider postamble; and the live conversation regression is documented.


## Notes

**2026-07-17T00:50:33Z**

2026-07-16 fixed from live Pixel session ses_d94bbaad38276fc8a0c5076cfc6e2e9f, where a second successful read_source was followed by a provider refusal MODEL envelope. The worker now treats successful canonical source rendering as terminal and ignores later provider deltas/completion. A fake provider reproduces the exact postamble and proves no MODEL reaches TTS, events, persistence, or context. External source bypass remains green in artifacts/voice-conformance/20260717T004757Z/report.json.
