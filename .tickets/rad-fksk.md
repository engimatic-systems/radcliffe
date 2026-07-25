---
id: rad-fksk
status: in_progress
deps: []
links: [rad-bbep]
created: 2026-07-25T17:44:58Z
type: bug
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, playback, source, resume]
---
# Resume source reading after a conversational interruption

After a speech barge-in and a model exchange, 'pick up where we left off' restarts the source from sentence zero instead of resuming at the last heard boundary.

## Design

Resume position is keyed by tool call, not by content. The source envelope's message_id is source:{call_id} (tools.py), so segment ids are source:{call_id}:{index}. _persisted_source_boundary (pipeline.py) looks up playback_acknowledgements for the CURRENT envelope's segment ids; a fresh read_source call produces a brand-new namespace, finds nothing, and returns 0. The prior progress is intact but unreachable. Barge-in additionally nulls _paused_source (pipeline.py barge_in), and _finish_cancelled only preserves it for disposition=='paused', so the in-memory handle is gone too.

Option A (deterministic floor): resolve resume position by content. heard: envelopes carry radcliffe.source.content_sha256 and radcliffe.playback.boundary_index/complete, so the furthest heard boundary for a document can be recovered across any number of read_source calls. Identical bytes produce an identical sentence split, so boundary indices are portable across calls.

Evidence: ses_853b2faaef57834a4319a00bd0adb0f6. read_source toolu_0169Yqof reached heard bi=9 complete=true (segment 10 started then interrupted). 'Okay, pick up where we left off' produced read_source toolu_01AQBg, which restarted at bi=0. Same content_sha256 ff01766478f8 across all three reads.

## Acceptance Criteria

Resume position is resolved from the durable heard ledger by content hash, not by call id; a bare read_source of a partially heard document continues at the first unheard sentence; a fully heard document does not silently replay from zero; pause/resume within a single call keeps its existing exact-boundary behaviour; a partially heard sentence replays from its own start exactly once; and tests cover interrupt-then-converse-then-resume across distinct call ids.


## Notes

**2026-07-25T17:55:55Z**

2026-07-25 implemented content-keyed resume. radcliffe_domain.projection gains heard_boundary/resume_boundary, which recover the furthest heard sentence for a document from the heard: ledger by content hash. VoicePipeline._persisted_source_boundary now takes the max of the existing per-segment acknowledgement lookup and the content-keyed lookup, clamped to the render plan, so pause/resume keeps its exact prior behaviour while a fresh read_source call for the same bytes continues instead of restarting. The tool_end path resolves a start boundary before speaking rather than always starting at zero.

Verified against the reported failure: services/media-worker/tests/test_source_resume.py replays ses_853b2faaef57834a4319a00bd0adb0f6 from fixtures/sources/gwern-nicotine.md with no download or background job. Reverting the resolution to the old behaviour fails the plain-resume case with 'assert 0 == (9 + 1)', the same restart-at-sentence-zero seen on the Pixel. Full make check green: 389 Python passed (1 skipped), 109 sidecar tests, Android unit tests and lint.
