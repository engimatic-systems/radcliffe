---
id: rad-86le
status: in_progress
deps: []
links: [rad-oy5k]
created: 2026-07-26T03:25:21Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, media, playback, latency, tts]
---
# Prefetch sentence audio so synthesis overlaps playback

Source reading pauses audibly between every pair of sentences. The pause is the synthesis latency of the next sentence, fully exposed, because synthesis and playback never overlap.

_speak_plan (pipeline.py) walks the render plan and calls _speak(..., wait_for_playback=True) per segment. Inside _speak the order is: await synthesizer.synthesize(), then await publisher.publish(wait_for_playback=True), which blocks until that sentence finishes playing. Only then does the loop synthesize the next one.

The current local provider makes this worse: it spawns two subprocesses per sentence (/usr/bin/say, then ffmpeg) and writes three temp files, so the fixed per-sentence cost is substantial.

This is structural, not a property of the TTS model. A faster model shortens the gap but cannot close it, because nothing overlaps. Fixing the pipeline first also isolates the variable for the planned Kokoro swap: land this, listen on the existing voice, then change models and attribute the difference honestly.

## Design

Run a bounded synthesis look-ahead: synthesize sentence N+1 while N is still playing, holding prepared audio in a small queue. Memory is negligible - 24 kHz mono 16-bit is 48 KB/sec, so a depth of two or three sentences is about a megabyte.

THE RULE THAT MATTERS: prefetch audio only, never playback registration. Synthesis is a pure function of text and delivery rate and is safe to run ahead. Registration is stateful and is not. _PendingPlayback construction, the events.playback_segment emission, and acknowledgement handling must all stay exactly where they are, in play order. If prefetch registered pending playbacks early, the heard ledger would mark sentences heard before they were spoken, silently corrupting every later resume and poisoning the boundary work rad-oy5k depends on.

Implementation shape: extract synthesis from _speak into a helper, add an optional prepared-audio argument to _speak so the publish/register/acknowledge path stays in exactly one place rather than being duplicated, and drive _speak_plan from a producer task feeding an asyncio.Queue whose maxsize provides the bound.

INVALIDATION - simpler than it first appears. Configuration changes (speech rate, and later voice) can only reach the pipeline through a user interruption, because only the model calls configure and the model only runs during a turn. An interruption cancels the reading turn, which unwinds _speak_plan, which cancels the producer and discards the queue. So the buffer's lifetime is bounded by a single _speak_plan call and no separate staleness check is required. This invariant must be stated in a comment and pinned by a test, because it is the thing that would break if a future change let configuration apply without interrupting. If that ever happens the hook is already present: cancel the producer.

Producer failures must not deadlock the consumer on an empty queue. The producer catches exceptions and enqueues them; the consumer re-raises on receipt.

Empty segments (navigation and link-only lines that normalize away) keep their current treatment: skipped without synthesis, with turn.next_source_boundary still advanced so resume positions stay anchored to the document.

Model speech is out of scope. _speak_model_chunk already passes wait_for_playback=False, so model chunks do not serialize against playback and do not exhibit the gap.

## Acceptance Criteria

Sentence-to-sentence silence during source reading is reduced to the playback handoff rather than the full synthesis time of the next sentence, and reading remains continuous at elevated speech rates. Synthesis of the following sentence demonstrably overlaps playback of the current one. No sentence is registered as pending playback, acknowledged, or recorded in the heard ledger before its audio is actually published - covered by a test asserting the heard ledger contains nothing for sentences that were only prefetched. An interruption discards all buffered audio: nothing synthesized ahead is played after a barge-in, and the sub-500ms interruption guarantee is unaffected. Resume positions and boundary indices are unchanged by prefetching, so existing resume and repositioning tests continue to pass untouched. Prefetch depth is bounded and configurable, and a synthesis failure surfaces as an error rather than hanging the reader. Verified by a Pixel listen-through of a multi-paragraph article judged continuous by the operator.

