---
id: rad-9e2z
status: closed
deps: []
links: [rad-l1jc, rad-rwfl, rad-enbx, rad-ghti]
created: 2026-07-14T04:58:51Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-3se2
tags: [voice, stt, privacy, testing]
---
# Benchmark and upgrade local STT with an Oxford-derived acoustic corpus

The 2026-07-13 Pixel run split and misheard the spoken Markdown regression prompt before later capturing it nearly exactly. The current large-v3-turbo configuration has no command-level WER gate or retained acoustic evidence, so local-provider quality regressions require manual discovery.

## Design

Build a seeded, local-only acoustic corpus using multiple synthetic voices, natural pauses, noise, command vocabulary, and the observed Cosmic touch prompt. Score normalized WER, intent-keyword preservation, segmentation, and transcription latency. Benchmark current decoding against higher-quality local Whisper variants on identical audio, select the measured quality/latency winner, and retain reports plus WAV fixtures without storing private phone audio by default.

## Acceptance Criteria

The corpus and seeds are reproducible; generated input audio, expected text, actual transcript, WER, required-keyword recall, segment count, and latency are retained in a local report; the observed dash/bold/Cosmic touch command is permanent coverage; short pauses, background noise, and multiple voices are covered; at least the current large-v3-turbo and a higher-quality local configuration are measured on identical samples; the chosen default meets documented WER and keyword thresholds without any cloud API; unit tests cover scoring and report logic; the live worker uses the selected model after restart.


## Notes

**2026-07-14T05:26:25Z**

Implemented and verified locally. The seeded seven-case corpus retains WAVs, references, transcripts, hashes, WER, keyword recall, model segments, and latency. large-v3-turbo without vocabulary prompting missed bold as full; full large-v3, Parakeet TDT v3, and Qwen3-ASR alternatives were also worse on the identical/degraded commands. large-v3-turbo with the Radcliffe initial prompt passed every case at 0.000 macro WER, 1.0 minimum keyword recall, and 0.303s median warm transcription. The persistent worker was restarted on that configuration. Evidence: artifacts/stt-conformance/20260714T052435Z/report.json; scoring/report tests pass.
