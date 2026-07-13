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

**2026-07-13T03:54:22Z**

Local privacy profile implemented and verified on the M2 Max: Pi streams qwen3:8b through Ollama on loopback; MLX Whisper large-v3-turbo completed a real local transcription; macOS Samantha produced 24 kHz mono PCM; and the supervised worker connected to the radcliffe-demo LiveKit room. Direct model smoke completed in 2.3s and an authorized read_source tool round trip completed in 9.6s. Pixel acoustic and five-turn latency evidence remain outstanding.

**2026-07-13T04:37:37Z**

Physical Pixel run exposed and local fixes now verify: LiveKit microphone audio reached endpointing, but MLX Whisper's file loader could not find ffmpeg in the LaunchAgent PATH and Pipecat cancelled the persistent pipeline after five idle minutes. STT now passes normalized PCM directly to MLX in memory, the worker uses PipelineWorker with no room idle expiry, sidecar cancellation is observed, and errors are contained. A live synthetic room returned local speech in 2.812s.
