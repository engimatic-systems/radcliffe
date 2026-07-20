---
id: rad-8uwy
status: open
deps: [rad-fvx9]
links: [rad-fvx9]
created: 2026-07-20T01:53:18Z
type: bug
priority: 2
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, android, voice, stt, whisper, regression]
---
# Stop recurring silent-audio Thank you turns

The Pixel again persisted unsolicited USER turns containing Thank you while the user was silent, regressing the earlier low-information Whisper rejection work.

## Design

Use the retained endpoint and transcription evidence to distinguish actual near-field speech from speaker echo, room noise, and low-information Whisper output. Strengthen admission using measured foreground speech and decoder confidence without phrase blacklisting or sacrificing legitimate short utterances.

## Acceptance Criteria

A regression test covers the two 2026-07-19 false turns and their measured evidence; silence, room tone, speaker echo, and sparse low-confidence candidates produce no USER envelope or model call; legitimate short speech including a deliberately spoken Thank you remains accepted across speaker and earbud routes; content-free diagnostics explain rejection; a physical Pixel silence run confirms the phrase no longer appears unsolicited.


## Notes

**2026-07-20T01:53:59Z**

2026-07-19 evidence: two unsolicited USER envelopes containing Thank you were persisted at 01:38:04Z and 01:38:38Z. The accepted candidates had only 550ms and 343.75ms foreground voice, 6 and 12 Silero speech windows, maximum speech probability 0.7162 and 0.914, and average log probability -1.0959 and -0.922; the current thresholds therefore admitted low-information audio twice.

**2026-07-20T05:30:50Z**

Implemented short-foreground decoder gating on commit 44ac82c without phrase blacklisting. The two retained 2026-07-19 evidence profiles (550 ms/6 windows/-1.0959 and 343.75 ms/12 windows/-0.922) are rejected silently with aggregate-only diagnostics, while deliberate short speech remains accepted. Generated-audio report artifacts/stt-rejection/20260720T053005Z/report.json passes all 15 cases: digital silence, room tone, post-AEC echo, background babble, sparse noise/spikes, five short commands, and deliberate 'Thank you.' over earbud and simulated speaker routes. Full make check passes (314 Python passed/1 skipped, 55 sidecar, Android unit/lint). Physical Pixel silence acceptance remains; no ADB device was attached.
