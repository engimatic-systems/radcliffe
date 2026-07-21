---
id: rad-fvx9
status: closed
deps: [rad-9e2z]
links: [rad-rwfl, rad-enbx, rad-ghti, rad-9e2z, rad-0mws, rad-8uwy, rad-6dal]
created: 2026-07-15T04:08:39Z
type: bug
priority: 1
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, milestone-2, stt, whisper, endpointing, privacy]
---
# Reject Whisper silence hallucinations before they become user turns

Whisper can decode low-information room tone or sparse noise as common phrases such as 'Thank you.' The 2026-07-14 Pixel run produced two false USER envelopes: a 30.04s maximum endpoint with only 343.75ms detected voice became 'That's it. Thank you.', and a 1.65s endpoint with 343.75ms detected voice became 'Thank you.'.

## Acceptance Criteria

A retained synthetic corpus covers digital silence, room tone, playback echo, a 30s mostly-silent maximum endpoint with sparse voiced spikes, and short legitimate utterances; endpoint/STT confidence policy uses measured voice occupancy plus Whisper segment evidence such as no-speech probability and log probability rather than a phrase blacklist alone; rejected low-information audio creates no USER envelope or model call; legitimate short speech across supported voices remains above recall thresholds; rejection reasons and aggregate confidence are logged without retaining private phone audio; and the Pixel speaker-path regression no longer emits unsolicited thank-you turns.


## Notes

**2026-07-15T04:12:34Z**

Implementation evidence is in the local worker log: both false turns contained only 343.75 ms detected voice; one came from a 30.04 s maximum-duration endpoint and one from a 1.65 s endpoint. Treat this as sparse-noise/echo plus low-information Whisper decoding, not literal digital-silence transcription, and gate on voice occupancy plus decoder confidence rather than phrase text.

**2026-07-15T04:50:58Z**

Implemented layered false-turn admission on 2026-07-14: subminimum RMS bursts now reset after ordinary silence; sparse 30s candidates require 5% voice occupancy; local candidates require Silero speech windows; Whisper output is rejected on non-finite evidence, compression ratio, average log probability, or no-speech probability without any phrase blacklist. Synthetic report artifacts/stt-rejection/20260715T044752Z/report.json passes silence, room tone, post-AEC echo, short noise, 30s sparse spikes, and six short legitimate voices including 'Thank you.' Full live voice report artifacts/voice-conformance/20260715T044840Z/report.json also passes. Remaining acceptance evidence is an ambient-silence/speaker-path Pixel run confirming no unsolicited user turn.

**2026-07-16T20:30:03Z**

2026-07-16 noisy-room follow-up: two Pixel runs showed RMS background candidates lasting 4-12s and harmless candidates cancelling audible output before Silero rejection. Implemented dual candidate/foreground endpoint thresholds, a separate higher near-field barge-in threshold, sustained debounce plus Silero evidence before flush, a 2s unconfirmed-background bound, foreground-based trailing silence, deterministic Whisper temperature, and aggregate RMS telemetry. Synthetic report artifacts/stt-rejection/20260716T201840Z/report.json passes nearby babble rejection and foreground speech over continuing babble. Full report artifacts/voice-conformance/20260716T202552Z/report.json passes: 2.0s babble created no interruption/user envelope and preserved 92 active output frames with one active sidecar turn; deliberate speech acknowledged in 0.389s and old audio stopped in 0.250s. Remaining gate: physical Pixel speaker and earbud verification.

**2026-07-16T21:45:39Z**

2026-07-16 physical Pixel acceptance: the tester ran speaker and earbud follow-ups after the babble/debounce change and reported that interruption works, with no unsolicited silence/Thank-you turn reported. A subsequent short-acknowledgement regression added 600 ms bounded endpoint pre-roll; the retained quiet-onset US-male command decodes as 'Okay, count from 1 to 50.' while silence, room tone, echo, babble, and sparse noise remain rejected. Evidence: artifacts/stt-rejection/20260716T214212Z/report.json and artifacts/voice-conformance/20260716T214321Z/report.json.
