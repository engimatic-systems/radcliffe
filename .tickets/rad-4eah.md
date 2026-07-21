---
id: rad-4eah
status: closed
deps: [rad-7h1e]
links: []
created: 2026-07-17T23:28:22Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-1wji
tags: [radcliffe, agent-control, tts, voice]
---
# Add bounded per-snippet speech delivery controls

Allow an agent response to speak one snippet quickly, slowly, or normally without changing the text and without leaking the directive into speech or conversation content.

## Design

Represent delivery as structured snippet metadata. Map named presets and any supported numeric rate to a provider-independent bounded range, then translate at the local TTS adapter. A snippet override expires at its playback boundary; session defaults remain separate.

## Acceptance Criteria

Slow, normal, and fast requests map to documented bounded local-TTS rates; optional numeric input is clamped or rejected deterministically; delivery metadata is never spoken or persisted as message text; canonical source text and hashes remain unchanged; one snippet cannot leak its rate into the next; interruption, pause, persisted-boundary resume, and replay retain correct delivery; unsupported providers fail visibly or use a documented normal fallback; tests verify text identity, ordering, duration bands, scope precedence, cancellation, and exact-source behavior; and the Android timeline can indicate a non-default delivery instruction accessibly.


## Notes

**2026-07-18T05:08:53Z**

Implemented render-v2 per-boundary speech delivery metadata with slow/normal/fast/very-fast and bounded numeric rates; read_source routing; local Apple/OpenAI TTS mapping; playback protocol propagation; deterministic replay/resume and legacy-provider fallback; Android durable/live accessible indicators; and conformance coverage for identity, bounds, scope, idempotency, pause/cancellation, exact source, provider behavior, and API/UI projection. Full make check passed: 195 Python passed (1 skipped), 26 sidecar tests, Android unit tests and lint.
