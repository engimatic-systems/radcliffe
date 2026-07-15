---
id: rad-fvx9
status: open
deps: [rad-9e2z]
links: [rad-rwfl, rad-enbx, rad-ghti]
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
