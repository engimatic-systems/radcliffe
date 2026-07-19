---
id: rad-kp6y
status: closed
deps: []
links: []
created: 2026-07-19T02:43:55Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-nhai
tags: [radcliffe, agent, jobs, executor, sandbox, concurrency]
---
# Drive durable jobs through a separate executor context

Claim accepted jobs in a background supervisor, run each attempt through an explicit user-supplied executor command and private per-job workspace, and report only bounded lifecycle events while foreground turns continue.

## Design

Use the durable lease as the ownership boundary. Launch an opt-in JSON-lines executor with a scrubbed environment and private workspace; stream primary instructions and stable follow-up frames over stdin rather than argv or environment; parse only allowlisted progress/result frames; heartbeat leases, deliver live follow-ups once by ID, enforce runtime and output ceilings, terminate on committed cancellation, and retain a private transcript separate from conversation events.

## Acceptance Criteria

run_job still returns before executor completion; executor activation is explicit and disabled by default; one process owns one leased attempt; instruction and follow-ups are absent from argv, inherited secrets, telemetry, and conversation events; per-job workspace and transcript are owner-only; live follow-ups reach the running process once; cancellation and runtime/output limits terminate work and publish stable results; expired leases recover after restart; and deterministic fake-process tests cover concurrent foreground acceptance, progress, success, follow-up, cancellation, malformed output, timeout, and crash.


## Notes

**2026-07-19T02:57:50Z**

Implemented an explicit opt-in JSON-lines job executor supervisor: separate leased attempts, scrubbed child environments, private mode-0700 workspaces and capped mode-0600 output transcripts, stdin-only primary/follow-up context, live stable-ID follow-up delivery, allowlisted lifecycle frames, foreground-independent concurrency, readiness diagnostics, process-group cancellation, runtime/output bounds, waiting/requeue, crash handling, and expired-lease recovery. The configured command is documented as a user-supplied VM/container/sandbox wrapper; executor mode remains disabled by default. Verification: make check passed (259 Python passed, 1 skipped; 41 sidecar tests passed; Android unit tests and lint passed).
