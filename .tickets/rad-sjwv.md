---
id: rad-sjwv
status: in_progress
deps: []
links: [rad-86le]
created: 2026-07-26T04:05:45Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, tts, speech, voice, mlx]
---
# Replace macOS Samantha with Kokoro bf_emma for spoken output

Spoken output uses /usr/bin/say with the macOS "Samantha" voice, piped through ffmpeg. Samantha is a pre-neural concatenative voice; no amount of tuning makes it sound current, and the operator is tired of it. The path also spawns two subprocesses and writes three temp files per sentence.

Replace it with Kokoro-82M running under MLX, starting with the British female voice bf_emma.

## Design

Kokoro is an 82M-parameter StyleTTS2 derivative, Apache 2.0, whose native output is 24 kHz mono - exactly the format the publisher already expects, so no resampling is introduced.

WEIGHT PROVENANCE - checked rather than assumed. mlx-audio loads prince-canuma/Kokoro-82M, which is not the official release; the official one is hexgrad/Kokoro-82M. mlx-audio cannot consume the official repo directly because it ships PyTorch .pth and the loader wants safetensors or npz. Comparing the two tensor by tensor: 548 tensors on each side, zero keys missing, and every tensor bitwise-equal within 1e-5 once the DataParallel ".module." prefix is stripped. The MLX repo is a pure container reformat of the official weights, so using it is using the official weights.

MEASURED on this M2 Max, warm, per sentence:
  mlx-audio (MLX)              ~150ms   25x realtime
  official kokoro pkg on CPU   ~570ms    7.9x realtime
  official kokoro pkg on MPS   ~700ms    5.3x realtime
MLX wins by roughly 3x over the best official-package path, which matters for the first sentence of a conversational reply since that latency is not hidden by rad-86le's look-ahead.

Cold start is ~15s (spaCy load, pipeline construction, Metal kernel compilation), so prepare() must load AND exercise the model. PreparableSpeechProvider already exists for exactly this and is already awaited before the worker reports input readiness, which is how Parakeet avoids the same problem.

Shape: KokoroSpeechProvider subclasses ParakeetAppleSpeechProvider to keep Parakeet STT unchanged and override synthesize only. Selected by RADCLIFFE_SPEECH_PROVIDER=kokoro; voice and model are environment-tunable. Synthesis runs in a worker thread under a lock, because MLX Metal state is process-global and the caller is async.

lang_code is derived from the voice name rather than hardcoded: Kokoro voices are prefixed by language then gender, so bf_emma is British female and implies lang_code "b". Hardcoding "b" would mispronounce every American voice.

Out of scope: switching voices mid-session by tool command. That needs the prefetch buffer invalidated on a voice change, and rad-86le establishes that configuration changes only arrive through an interruption which already discards the buffer. Worth re-verifying when the tool lands.

## Acceptance Criteria

Spoken output uses Kokoro bf_emma rather than macOS Samantha, and the operator judges it naturalistic and understandable at elevated speech rates. Synthesis stays comfortably ahead of playback so reading remains continuous. The requested speech rate range of 0.75x to 1.5x is honoured through Kokoro's own speed control rather than by resampling. Audio reaches the publisher as 24 kHz mono 16-bit PCM, unchanged in format from the previous provider, and the exact-source guarantee is untouched since synthesis consumes canonical text and produces audio only. Model weights are verified equal to the official hexgrad release. Cold-start cost is paid in prepare() before the worker reports readiness, so the first utterance of a session is not slow. The provider is selectable by environment variable and the previous say-based path still works when selected. Verified by a Pixel listen-through of a multi-paragraph article.

