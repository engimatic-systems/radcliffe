---
id: rad-bbep
status: in_progress
deps: [rad-fksk]
links: [rad-fksk]
created: 2026-07-25T17:45:09Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, playback, source, resume, sidecar]
---
# Give the model an addressable source reading position

Expose the playback boundary of heard source text to the conversational model and let it request a start boundary, so repositioning ('back up a bit', 'resume', 'skip ahead') is exact rather than a fresh read from sentence zero.

## Design

The heard buffer already reaches the model: _persist_heard_source appends heard: envelopes (SourceKind.TEXT_SOURCE) and context_projection includes them. Their metadata carries radcliffe.playback.boundary_index, radcliffe.playback.complete and radcliffe.source.content_sha256, and context_projection copies all of it into packet.annotations. But label() in services/agent-sidecar/src/orchestrator.ts renders only the locator annotation, so the model reads the text with no position marker and cannot express where to resume.

Two changes: (1) label() includes the read position for source packets; (2) read_source / read_job_source accept an optional start_at_boundary and plumb it to _ActiveTurn.next_source_boundary, which _speak_plan already honours. Boundary indices are portable across calls because identical bytes produce an identical sentence split.

Prefer an explicit integer boundary over phrase matching: the model can see the number, and it survives STT and sentence-splitting where a spoken phrase would not.

## Acceptance Criteria

Source context packets show the model the boundary already read and the next boundary to resume from; read_source and read_job_source accept an optional bounded start_at_boundary through the sidecar schema, argument preparation, tool gateway, and control-plane details; playback begins at that boundary without respeaking earlier sentences; an out-of-range boundary fails visibly rather than silently restarting; the argument round-trips through tool-call replay; and tests cover resume, backing up, skipping ahead, and out-of-range rejection.


## Notes

**2026-07-25T17:55:55Z**

2026-07-25 implemented the addressable reading position. label() in services/agent-sidecar/src/orchestrator.ts now renders the boundary already read and the next boundary to resume at for text_source packets, so the heard buffer the model already receives carries a usable position instead of dropping it. read_source and read_job_source accept an optional startAtBoundary through the sidecar schema, argument preparation (including the integer-valued string local models emit), the control-plane request models, the tool gateway, and the tool result details; it round-trips through tool-call replay so a duplicate call resumes identically. An explicit boundary overrides the ledger, which is what makes backing up and skipping ahead expressible; a boundary past the end of the source fails visibly rather than silently restarting.

Covered by services/media-worker/tests/test_source_resume.py (plain resume, explicit resume, back up, skip ahead, out-of-range rejection, unheard document), packages/domain-python/tests/test_resume_boundary.py, and sidecar label/argument tests.
