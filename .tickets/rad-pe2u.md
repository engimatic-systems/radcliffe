---
id: rad-pe2u
status: closed
deps: []
links: []
created: 2026-07-17T00:45:17Z
type: bug
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, voice, content-policy, determinism]
---
# Enforce the no-emoji model-output policy deterministically

The live local model emitted emoji despite the system prompt. Enforce the existing No Emojis requirement at Radcliffe model-output boundaries rather than relying on provider compliance.

## Acceptance Criteria

Emoji code points and presentation joiners are removed before model text reaches TTS, durable MODEL envelopes, or Android UI; ordinary Unicode text is preserved; streamed deltas and final model_complete text produce one consistent canonical result; tests cover common pictographs, variation selectors, joined sequences, and a provider that ignores the prompt; and the external no-emoji assertion remains green.


## Notes

**2026-07-17T00:50:34Z**

2026-07-16 fixed from the same live Pixel session, where Ollama emitted emoji in MODEL envelopes despite the No Emojis system prompt. The worker now removes common pictographs, variation selectors, keycap marks, and emoji joiners before TTS/events/persistence while preserving ordinary Unicode joiners. Provider-misbehavior coverage passes; external no-emoji assertion is green in artifacts/voice-conformance/20260717T004757Z/report.json.
