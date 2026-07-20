---
id: rad-0ch6
status: open
deps: []
links: [rad-20nk, rad-cnrg]
created: 2026-07-20T19:47:56Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-cnrg
tags: [voice, exact-output, response-detail, enumeration, p0]
---
# Preserve exact requested enumerations despite brief-response policy

When asked to count slowly from 1 to 60, the model envelope itself abbreviated the answer with an ellipsis after 'One, two, three' rather than producing the requested sequence. After the user said to keep going, the model resumed at forty-three and continued to sixty, inventing progress that never appeared in the durable response. Every synthesized segment completed, so this loss occurred before TTS/playback. The session's effective response_detail was brief, and the sidecar appends 'Keep this response brief'; there is no deterministic policy or validator requiring completeness for exact enumerations.

## Design

Treat explicit exact-output requests—enumerations, character-by-character readings, identifiers, hashes, URLs, JSON, paths, timestamps, and explicit exact-reading exceptions—as fidelity constraints that override brevity and stylistic compression. Make the contract visible to the model and enforce it deterministically before synthesis: validate sequence coverage/order and retry or report a bounded, truthful limitation rather than emitting ellipses or hallucinating omitted progress. Ensure continuation state is derived from durable emitted content, not intended content. Fold the scenario into the spoken-output corpus associated with rad-cnrg.

## Acceptance Criteria

1. Under brief, normal, and detailed response settings, 'Count from 1 to 60' emits every integer exactly once, in order, in the durable model envelope and audible output. 2. 'Count slowly from 1 to 60' preserves completeness while applying only a delivery-rate change. 3. Exact-output requests are not replaced by ellipses, summaries, 'and so on', or fabricated continuation offsets. 4. If an output limit prevents completion, the assistant states the truthful stopping point and a continuation starts after the last durably emitted item. 5. Deterministic validation covers missing, duplicated, reordered, and abbreviated sequence items before TTS. 6. The exact-output corpus includes numbers, IDs, hashes, URLs, JSON, paths, timestamps, and explicit verbatim/exact-reading exceptions across local and Haiku profiles.


## Notes

**2026-07-20T19:48:12Z**

Observed 2026-07-20 in ses_eb23d75121af2c72f8c55641adab1136: durable model sequence 5 says 'One, two, three...' and sequence 8 resumes 'Forty-three, forty-four, ... sixty.' Effective response_detail was brief revision 2; all generated playback segments completed. The omission therefore originated in model output before TTS, with the brief-policy injection as a causal prompt conflict.
