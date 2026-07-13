---
id: rad-ghti
status: in_progress
deps: [rad-wnwh, rad-58mt, rad-8i08, rad-0ekb, rad-ihso, rad-g7di]
links: []
created: 2026-07-13T01:52:53Z
type: task
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, milestone-1, voice]
---
# Implement the streaming VAD, STT, Pi, and TTS voice pipeline

Connect LiveKit microphone audio to turn detection and STT, send canonical user turns through AgentOrchestrator, stream model output into sentence-level TTS, and publish cancellable agent audio.

## Acceptance Criteria

The Python worker subscribes to target-participant microphone audio; VAD and endpointing produce one final STT transcript and USER envelope per completed turn; ContextProjection sends the turn to the Pi sidecar; MODEL text is segmented and synthesized without waiting for the complete response; TTS audio is published through LiveKit with segment IDs understood by the playback ledger; barge-in cancels stale synthesis and publication; fake-provider contract tests cover every boundary; one measured provider configuration produces meaningful first audio within five seconds for five consecutive Pixel demo turns; and the selected M1 STT, model, and TTS configuration is documented.

## Notes

**2026-07-13T03:02:56Z**

Implementation is committed: executable Pipecat LiveKit worker, energy endpointing, OpenAI STT/TTS adapters, Pi sidecar streaming, playback acknowledgements, exact-source bypass, cancellation, and fake-provider tests. Remaining acceptance evidence is five measured provider turns under five seconds on the deployed Pixel path.
