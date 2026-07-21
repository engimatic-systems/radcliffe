---
id: rad-ncdz
status: open
deps: [rad-l1jc]
links: [rad-l1jc, rad-lezo]
created: 2026-07-13T01:52:53Z
type: feature
priority: 0
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, milestone-1, demo, operator-final-check]
---
# Milestone 1: demo Android voice and deterministic source reading

Integrate the foundation components into the single verifiable Pixel demo that gates the first major PR.

## Acceptance Criteria

From a clean documented setup, the target Pixel connects to the self-hosted LiveKit topology; an ordinary spoken question yields a visible transcript and spoken frontier-model answer within five seconds; speech or tap interruption stops audible output within 500 ms and accepts the next turn; asking to read the supplied Markdown fixture causes Pi to request read_source and the Tool Gateway to send canonical source text directly to TTS with visible provenance; app and system controls pause and resume at the persisted sentence boundary; ending and restarting leaves no orphaned operation; the independent suite produces a passing report with audio, latency, envelope/state timeline, canonical-text proof, and tool audit; and the README contains a concise demo script.

## Notes

**2026-07-13T03:54:22Z**

An installable debug APK and LAN setup sheet are available in /Users/ravd/agent-share. The local Ollama/Whisper/TTS stack and worker are running; the remaining milestone gate is the physical Pixel demo and its acoustic/interrupt/TURN evidence.

**2026-07-14T02:46:33Z**

User physically verified ordinary Pixel voice conversation and basic app controls on 2026-07-13. Remaining M1 acceptance is the exact read_source/provenance phone scenario plus stricter captured latency, persisted-boundary resume, lock-screen/hardware, and network-path evidence.

**2026-07-15T03:54:21Z**

2026-07-14 automated milestone evidence now includes the exact source plus persisted-boundary pause/resume: pause 3ms, audible stop 67ms, resume audio 535ms, one source envelope/tool execution, final boundary 4. Next manual gate is frontend/device behavior on the existing Pixel app: visible source provenance and app/notification pause-resume while hearing the fixture.

**2026-07-15T04:12:34Z**

2026-07-14 Pixel source-read failure was not an OS permission failure: Whisper/model supplied the literal spoken path 'Radcliffe dash demo dot MD'. Added literal-first, narrowly scoped spoken Markdown filename normalization while preserving original audited arguments and rooted traversal controls. Full suite passes and a deployed control-plane smoke resolved the request to fixtures/sources/radcliffe-demo.md.
