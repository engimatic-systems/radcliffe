---
id: rad-cnrg
status: closed
deps: [rad-f08j, rad-mz5a]
links: [rad-f08j, rad-mz5a, rad-20nk, rad-0ch6]
created: 2026-07-20T03:00:01Z
type: task
priority: 3
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, voice, prompt, tts, ux, research, operator-autonomous]
---
# Explore and prevent speech-hostile generated output

Generated conversational responses can contain identifiers and machine-oriented strings that are technically correct but laborious or confusing to hear, such as job IDs and hashes. The prompt and rendering policy need a broader inventory of these failure modes.

## Design

Build a representative spoken-output corpus covering hashes, UUIDs, job and message IDs, URLs, paths, timestamps, stack traces, serialized JSON, long numbers, code, citation machinery, repeated punctuation, and acronym-heavy metadata. Evaluate prompt instructions first, then bounded deterministic detection or alternate visual-only rendering where prompts are insufficient. Preserve exact canonical source reading and explicit user requests to spell or read machine text.

## Acceptance Criteria

The ticket documents failure categories and intended spoken alternatives; local and cloud prompt evaluations measure recurrence across representative turns; ordinary generated speech avoids unrequested hashes, IDs, raw URLs, paths, serialized objects, and other machine-oriented strings while the timeline retains useful visual detail; explicit read-it-exactly requests and canonical read_source rendering remain unchanged; accessibility review covers cases where visual-only substitution is inappropriate; and follow-up implementation tickets are created for any remedy too broad for one change.

## Notes

**2026-07-21T04:11:59Z**

Implemented a split display/spoken model-output policy. Provider text remains in the durable model envelope and timeline; complete sentence/line segments receive bounded TTS alternatives for hashes, IDs/UUIDs, URLs, paths, timestamps, stack traces, serialized data, long numbers, code, citation machinery, repeated punctuation, and acronym-heavy metadata. Category-only telemetry and envelope metadata retain no matched content. Exact/verbatim/character requests bypass substitution, and canonical read_source rendering remains unchanged. Accessibility behavior and alternatives are documented in docs/spoken-output-policy.md. Follow-ups: rad-2e0w owns interruption-safe display/spoken partial-boundary mapping; rad-c2tv owns a physical-phone TalkBack affordance and exact-reading action. Local tests: 94 sidecar and 35 pipeline tests pass. Final real-profile corpus: Qwen kept 8 ordinary machine details visually and Haiku kept 11; ordinary spoken recurrence was zero on both, while two explicit exact exceptions were preserved on each. Qwen used 14717 input / 824 output tokens. Final Haiku pass used 44525 input / 1115 output tokens at estimated USD 0.050100; including the safe evaluator-calibration pass, this ticket used estimated USD 0.103944. Reports: artifacts/spoken-output/qwen-report.json and artifacts/spoken-output/haiku-report.json. Temporary sessions ended and devices revoked.
