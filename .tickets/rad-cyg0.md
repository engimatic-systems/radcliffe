---
id: rad-cyg0
status: in_progress
deps: []
links: []
created: 2026-07-19T20:46:12Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-nhai
tags: [radcliffe, operations, readiness, jobs, phone-testing]
---
# Gate phone-test handoffs on deployed feature readiness

Prevent APK checkpoints from being handed to a physical-device tester when the live sidecar/control-plane processes are stale or the required opt-in executor is unavailable.

## Design

Add a content-free deployed-capability/version handshake across the sidecar, control plane, and local operations report. A feature-specific phone preflight must inspect the running processes rather than the checkout, require the expected typed tools and API surface, and distinguish tool registration from executable sandbox availability. Update the phone-test runbook only as part of the enforced gate.

## Acceptance Criteria

A deterministic preflight fails before APK handoff when the running sidecar omits run_job/manage_job, the control plane lacks the background-job contract, the running revision differs from the expected checkout/build, or the job executor is disabled/unhealthy; passing proves a synthetic no-provider job can be accepted and driven through a configured test wrapper without exposing content or making a paid request; operations and documentation name the corrective restart/configuration action; automated tests reproduce the stale-Friday-sidecar/current-checkout failure and an executor-disabled failure; no credentials, prompts, raw session IDs, or transcripts enter readiness output.

## Notes

**2026-07-19T20:47:50Z**

Live reproduction on 2026-07-19: /readyz returned HTTP 200 ready with database, sidecar liveness, LiveKit, source-root, and worker-supervisor checks only, even though the sidecar/control processes predated the job feature and the configured environment had no executor. The current conversation advertised only list_sources/read_source; no job tool call, background job, or job command was recorded.
