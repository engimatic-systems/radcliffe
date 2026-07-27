---
id: rad-f08j
status: closed
deps: [rad-gf5x, rad-i6i7]
links: [rad-96c5, rad-cnrg, rad-rmfz, rad-fdtf]
created: 2026-07-20T03:00:01Z
type: bug
priority: 1
assignee: coopwilliams
parent: rad-nhai
tags: [radcliffe, jobs, voice, prompt, android, ux]
---
# Keep background-job acceptance acknowledgements brief

After run_job succeeds, the conversational model reads a long machine-oriented summary including the job ID and other controls. The spoken acknowledgement should communicate only that work started; detailed lifecycle metadata already belongs in the Android job card.

## Design

Give the model a bounded acknowledgement contract for successful run_job and manage_job mutation calls and keep detailed tool results available for subsequent manage_job reasoning without inviting them into ordinary speech. The sentence describing the tool action should use only a few words, such as I started it or I requested cancellation. Any additional useful context requested by the user must follow normally instead of being swallowed. Preserve visible typed lifecycle details and provide a concise actionable failure response when acceptance fails.

## Acceptance Criteria

Successful run_job acceptance and manage_job mutation describe the tool action in exactly one short spoken sentence and never speak the job ID, notification policy, priority, budget, runtime, revision, or serialized tool result. Additional useful foreground context from the model is delivered after that sentence without truncation. The Android job card still receives the canonical typed metadata, manage_job can use the exact ID later without the user repeating it, replay does not duplicate the acknowledgement, failure remains clear, and local plus Haiku prompt/tool-result tests cover acceptance, replay, error, and mixed acknowledgement-plus-context paths.


## Notes

**2026-07-20T03:28:10Z**

Implemented and deployed in b1bf61e: run_job tool-result prompting now requires exactly 'I started it.', sidecar output is canonically bounded on success/failure, and exact job IDs persist as deduplicated model-only context hidden from the Android message timeline. make check passed (300 Python, 1 skipped; 54 sidecar; Android unit/lint), operations and background-job preflight are ready, and a bounded live Haiku run_job turn emitted only 'I started it.' Physical phone confirmation remains.

**2026-07-20T03:40:42Z**

Refined in 11ae071 after user review: successful run_job and manage_job mutations now instruct the model to describe the tool action in one short sentence, then deliver all additional requested context normally. Machine-oriented mutation tool results no longer expose IDs or serialized metadata to the response model, while durable internal job references remain available for later exact management. Failures and omitted/mismatched tool calls remain fail-closed. Full make check passed (300 Python, 1 skipped; 54 sidecar; Android unit/lint). Live Haiku mixed-response acceptance emitted: 'Background job launched. The foreground conversation remains available for your next request.'

**2026-07-27T04:43:08Z**

Reopened after 2026-07-26 Sonnet 5 phone UAT and a revised operator requirement: after a successful run_job, the foreground response should be only a terse acknowledgement, with no follow-on context or suggestions. Three native run_job trials succeeded but spoke 96, 162, and 206-character acknowledgements. The immediate tool-result instruction still says 'Then provide any additional useful context the user requested; do not omit it,' directly conflicting with the desired bounded acknowledgement. The active source edit also was not present in the running dist bundle and currently omits a string-concatenation '+', truncating RADCLIFFE_SYSTEM_PROMPT in source tests. Prefer restoring a deterministic sidecar-owned success acknowledgement and regression coverage over relying on another model prompt.

**2026-07-27T04:57:01Z**

2026-07-26 physical-phone acceptance after rebuilding and restarting the sidecar from the active source: session ses_1606ba8a43d29f5ba7463625bd551ba4 launched run_job and spoke only the single acknowledgement 'Working on grabbing the Gettysburg Address for you.' Operator confirmed the revised acknowledgement behavior passes. Close.
