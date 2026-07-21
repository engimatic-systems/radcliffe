---
id: rad-nhai
status: in_progress
deps: [rad-7h1e]
links: []
created: 2026-07-18T00:47:41Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-tg8o
tags: [radcliffe, agent, jobs, voice, concurrency, events]
---
# Keep foreground voice responsive during background agent jobs

Allow a conversational session to remain fully interactive while one or more research, download, coding, or analysis agents work asynchronously, then announce useful results without talking over the user.

## Design

run_job returns an accepted job ID immediately rather than holding the foreground Pi tool turn open. Each job owns a separate agent context, operation generation, sandbox process, artifacts, cost budget, and durable lifecycle. Progress and completion enter the conversation as typed JOB events through the deterministic message-interception seam. A foreground scheduler decides when an event may be spoken.

## Acceptance Criteria

Starting a job returns promptly and the foreground voice loop continues to accept, answer, interrupt, pause, and resume ordinary turns while the job runs; users can ask status, change priority, add a follow-up, cancel, or leave the job running; multiple jobs cannot violate the one-owner foreground generation invariant; job commands and raw outputs remain in the job context while the conversation receives bounded status, promoted findings, source entities, and artifact references; completion persists across app, sidecar, worker, and control-plane restart; notification policy supports immediate, next-safe-pause, silent, and user-waiting modes; completion never speaks over detected user speech or stale agent audio; references such as a named paper resolve against durable result entities or trigger clarification; follow-up reading can render an exact abstract from a hashed source while analysis answers cite retrieved passages and label inference; and conformance proves concurrent chat, quiet waiting then proactive wake-up, interruption, cancellation, crash recovery, ambiguous reference handling, and two simultaneous jobs.


## Notes

**2026-07-19T02:22:23Z**

Implementation is staged as bounded vertical slices. rad-gf5x owns the typed durable lifecycle, idempotent acceptance and commands, execution leasing, privacy boundary, and restart tests. Later slices will attach the sandbox executor and separate agent context, safe-pause notification injection, promoted result/source resolution, Android controls, and end-to-end concurrent voice conformance.

**2026-07-19T02:44:02Z**

Started rad-kp6y for the opt-in separate executor context: scrubbed process boundary, private workspaces/transcripts, stdin job/follow-up frames, durable heartbeats, cancellation, and runtime/output limits.

**2026-07-19T03:01:46Z**

Started rad-96c5 for the safe-pause notification slice: durable session-ordered metadata events, restart-safe delivery dispositions, fixed allowlisted speech, and conservative foreground ownership gates.

**2026-07-19T03:22:17Z**

Completed rad-96c5: background JobEvent visibility is session-ordered and reconnectable, while fixed-phrase speech uses durable at-most-once dispositions and the existing foreground owner. Candidate user speech, transcription, active/paused turns, and unacknowledged current-generation playback all defer announcements; barge-in interrupts without restart duplication.
