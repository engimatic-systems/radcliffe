---
id: rad-nhai
status: closed
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

**2026-07-19T03:24:17Z**

Started rad-0eyx for the Android checkpoint: session-scoped JobEvent replay/deduplication, device-API hydration, accessible lifecycle cards, and idempotent follow-up/priority/cancel controls.

**2026-07-19T03:36:40Z**

Completed rad-0eyx: Android now hydrates and replay-deduplicates privacy-minimized background job status for the active session, shows accessible bounded job cards, and issues enrolled-device follow-up, priority, and cancellation commands without exposing private instructions or executor output. Full repository checks pass; physical-phone validation remains the checkpoint.

**2026-07-19T20:46:18Z**

Phone checkpoint failed before any job call: the live sidecar and control plane were still Friday processes predating run_job/manage_job, while the checkout and managed media worker were current; var/.env also has no opt-in job executor configuration. Filed P0 rad-cyg0 to make deployed capability/revision and executor readiness an enforced, content-free preflight before future APK handoffs.

**2026-07-19T20:52:08Z**

Started rad-erac to supply the missing concrete Colima/Docker + local-Ollama Unix job wrapper and deploy it to the protected local profile; rad-cyg0 separately enforces the preflight that would have caught the stale processes and disabled executor.

**2026-07-19T21:28:19Z**

The Unix executor is live behind the new deployed-feature preflight. An isolated live job was accepted immediately, a same-session foreground tool remained responsive, the local-Ollama/Colima agent completed and promoted its result, and the test session/device plus containers/network were cleaned up. Awaiting the physical Android lifecycle checkpoint on rad-erac.

**2026-07-20T05:52:00Z**

Implemented durable promoted job-source entities on commit 6e6b1f0. Background finish_job can bind a human source title and locator to a bounded UTF-8 Markdown artifact and SHA-256; succeeded JobEvents carry entity IDs. New read_job_source resolves an exact ID or unambiguous normalized title within the current session, rejects ambiguity/tampering/links/hash drift, and hands the verified source through direct canonical rendering without exposing private job transcripts. Full checks pass (316 Python passed/1 skipped, 56 sidecar, Android unit/lint). Deployed revision 6e6b1f0 passed the background-job readiness gate. Live local-Qwen + Colima evidence artifacts/background-job-sources/20260720T055123Z/report.json fetched a pinned public source via bounded egress, promoted one source, and rendered byte-identical content with SHA-256 b2580eab7825b9f22f790fb0edb7a6e239616e79907004adf36023c7ec4b9a4c; disposable session archived and test device revoked.

**2026-07-20T06:03:53Z**

Final autonomous acceptance on deployed commit 75d8bae: artifacts/background-job-findings/20260720T055825Z/report.json passes a real local-Qwen/Colima public acquisition with one exact hash-verified promoted source, two source-bound findings, and inference flags [false,true]. Full external LiveKit report artifacts/voice-conformance/20260720T060147Z/report.json passes after warm model load; first audio was 2.428s, interruption/source playback/restart/ownership checks passed. Deterministic suites cover prompt acceptance and foreground responsiveness; status/follow-up/priority/cancel grounding; running-cancel wins over late success; separate private executor context; crash/lease restart; all notification policies and safe-pause scheduling; interrupted announcement replay; ambiguous source failure; session-ordered simultaneous jobs; durable artifacts/entities; and exact canonical reading. The preceding cold run missed only first-turn latency at 5.268s, with all behavioral checks green, and the immediate warm rerun passed.
