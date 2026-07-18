---
id: rad-ghti
status: in_progress
deps: [rad-wnwh, rad-58mt, rad-8i08, rad-0ekb, rad-ihso, rad-g7di]
links: [rad-rwfl, rad-lo0t, rad-chaa, rad-214n, rad-nu8n, rad-l1jc, rad-enbx, rad-9e2z]
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

**2026-07-14T02:46:33Z**

Physical Pixel verification passed the basic local loop on 2026-07-13: microphone transcription, visible user/model messages, Ollama response, local TTS playback, tap interrupt, pause/resume, and end-to-idle all worked. The run also exposed a deterministic concurrency defect: an interrupted unanswered user request remained in projection and was answered alongside the next prompt; a naturally split utterance launched overlapping model turns and logged InvalidTransitionError (listening -> listening). Follow-up cancellation/serialization work will be tracked on the stacked next-milestone branch.

**2026-07-14T04:05:38Z**

2026-07-13 Pixel evidence: transcript-to-first-playback was approximately 6.44s, 5.34s, 12.93s, and 10.87s across four turns, excluding endpoint silence and Whisper time. Punctuation-poor Markdown and emoji were slowest. The latency/chunking follow-up is rad-chaa.

**2026-07-14T05:26:25Z**

Five consecutive real local-provider turns now pass through the black-box LiveKit harness at 2.680s, 2.144s, 2.221s, 2.186s, and 2.509s from synthetic speech end to heard output. Qwen3 hidden thinking was the latency defect: Pi now emits Ollama reasoning_effort=none, reducing the previous 4.5-5.5s run without weakening the 5s gate. STT/TTS/Ollama remain local. Full make check and make check-voice-local pass.

**2026-07-14T12:02:23Z**

2026-07-14 local-provider readiness no longer depends on an accidentally warm Ollama cache. Sidecar startup now loads qwen3:8b through Ollama's native API and retains it by default before reporting healthy. A forced-unload restart followed by the full live suite passed first audio at 2.651 s and all cancellation checks.

**2026-07-15T03:43:46Z**

2026-07-14 repeated local-provider evidence remains within budget after adding long-pause and exact-source scenarios. Latest full external run: ordinary/three exact turns 2.170-2.473s, replacement 2.245s, source audio 2.857s, tap acknowledgement 8ms, speech-barge acknowledgement 206ms; all checks passed in artifacts/voice-conformance/20260715T034110Z/report.json.
