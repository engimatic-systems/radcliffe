---
id: rad-u6lm
status: open
deps: []
links: []
created: 2026-07-27T15:46:22Z
type: feature
priority: 2
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, android, audio, accessibility, eyes-free, operator-final-check]
---
# Add subtle eyes-free audio cues for voice and tool state

Make the app operable without watching the screen by trying a small set of distinct, low-key earcons. Play cues when the session connects and begins listening, when endpointing finishes and the assistant begins thinking, when a background job starts, when a background job completes, and when read_source begins deterministic verbatim rendering. Generate the sounds in-repository rather than downloading third-party audio; each cue must be distinct and shorter than 500 ms. Keep the experiment easy to tune or remove, avoid masking speech, and do not let app-generated cues become user transcripts.

## Acceptance Criteria

The operator runs a Pixel test covering connected/listening, listening-to-thinking, background-job start, background-job completion, and read_source start; can distinguish every cue without looking at the phone; and likes the overall experience. Any cue the operator asks to change or remove is adjusted or removed before closure. All cues remain under 500 ms, use generated/local assets with no external licensing dependency, and do not disrupt speech, interruption, or transcription.

