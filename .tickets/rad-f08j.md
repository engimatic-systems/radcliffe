---
id: rad-f08j
status: open
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

Give the model a bounded acknowledgement contract for successful run_job calls and keep detailed tool results available for subsequent manage_job reasoning without inviting them into ordinary speech. Prefer one short natural sentence such as I started it or That is running. Preserve visible typed lifecycle details and provide a concise actionable failure response when acceptance fails.

## Acceptance Criteria

Successful run_job acceptance produces at most one short spoken sentence and never speaks the job ID, notification policy, priority, budget, runtime, revision, or serialized tool result. The Android job card still receives the canonical typed metadata, manage_job can use the exact ID later without the user repeating it, replay does not duplicate the acknowledgement, failure remains clear, and local plus Haiku prompt/tool-result tests cover acceptance, replay, and error paths.

