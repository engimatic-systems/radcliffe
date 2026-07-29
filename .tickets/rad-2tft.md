---
id: rad-2tft
status: closed
deps: []
links: []
created: 2026-07-13T01:36:39Z
type: epic
priority: 1
assignee: coopwilliams
tags: [radcliffe, foundation, epic, milestone-1, operator-interactive]
---
# Radcliffe foundation: reliable voice, provenance, and safe agent tools

Deliver a coherent vertical slice that proves the Radcliffe architecture on the target Pixel: native WebRTC voice, explicit session semantics, Pi-backed model/tool orchestration, and deterministic source reading through a safe read-only tool path.

## Acceptance Criteria

The epic is complete when all child tickets are closed and the documented Milestone 1 demo passes on the target Pixel: a user starts a native Android session, asks an ordinary voice question and hears a frontier-model answer within five seconds, interrupts audible output within 500 ms, invokes read_source through the Tool Gateway, hears canonical fixture text sent exactly to TTS with visible provenance, pauses and resumes from the persisted playback boundary, and ends/restarts the session without orphaned work. A reproducible demo report records latency, state, canonical-text, and tool-audit evidence.

## Notes

**2026-07-27T14:57:32Z**

2026-07-27 closeout sweep: all genuine M1 child tickets are closed. Evidence-complete component gates rad-0mws, rad-ghti, rad-h2lv, rad-l1jc, and rad-ncdz were closed from cumulative Sonnet 5 Pixel and conformance evidence. Post-M1 reliability and product enhancements were removed from the epic parent rather than being misrepresented as M1 blockers. Keep this epic in progress until the final consolidated Pixel run in docs/milestone-1-acceptance.md passes; then attach its session ID and close the epic.

**2026-07-27T15:14:30Z**

2026-07-27 consolidated Pixel run ses_9e9b88dffe96419826e8e03a4e75a1fc passed exact-once buffered speech, concise/successful background job execution, one-sentence ordinary response, barge-in replacement, canonical source hash 1882c547e099cae37966f860e73d9a11e59ba3951ce611280ccfdb90aebbf302, and persisted pause/resume. Voice-created conversation failed with three manage_session HTTP 409 responses because operation generation 11 was incorrectly compared with durable catalog generation 1. Track and retest only that fix in rad-g549 before closing this epic.

**2026-07-27T15:46:35Z**

2026-07-27 Milestone 1 accepted and closed. Consolidated Sonnet 5 Pixel evidence covers exact-once buffered speech, concise background jobs, sub-five-second ordinary response latency, sub-500ms interruption, canonical read_source SHA-256 1882c547e099cae37966f860e73d9a11e59ba3951ce611280ccfdb90aebbf302, persisted pause/resume, and final consecutive fresh-session handoffs at revision c76766d. Sessions ses_34c309e7a73d20a3c829b0e3b722e827 -> ses_f5226c87235fc08e94b622c9ce35a803 -> ses_2991b3679dd517dd9a9f07348e21129d each recorded applied/handed_off lifecycle events with stopped source workers and no inherited conversation memory. docs/milestone-1-acceptance.md is the evidence ledger. Operator approved closure.
