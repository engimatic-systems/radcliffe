---
id: rad-cyg0
status: open
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

**2026-07-19T21:28:19Z**

Implemented the enforced content-free phone preflight: live control/sidecar revisions and capabilities must match the checkout, run_job/manage_job must be advertised, the executor and configured image must be available, and a credential-free synthetic process-contract job must complete. Regression tests cover the stale sidecar, missing tools, revision mismatch, and disabled executor; live preflight and operations gates pass.

**2026-07-19T23:01:01Z**

Physical checkpoint exposed a remaining procedural gap: host-side revision/capability/executor checks passed while the Pixel's saved control origin was not tailnet-reachable. The Mac and Android Tailscale peers were online, the control listener was healthy, and no Pixel request reached the server. Added a dedicated tailnet-only HTTPS control endpoint on port 8443 without disturbing the existing dashboard, and updated the runbook to require the app Connection test plus Recent conversations load before calling a phone handoff ready.

**2026-07-20T00:00:49Z**

Checked the live Tailscale Serve allocation before sharing the endpoint, moved the Radcliffe tailnet-only HTTPS proxy from 8443 to the permitted available port 8090, verified /healthz, removed the obsolete 8443 handler, and preserved the existing 8083 dashboard.

**2026-07-20T00:13:19Z**

The phone passed control connection and Recent loading but exposed a second un-gated route: room credentials advertised the Mac LAN-only LiveKit URL, so Android signaling timed out. Revision ef59f64 adds a protected network override, tailnet WSS signaling, configurable advertised ICE/TCP+UDP ports, regression coverage, and an operations requirement for a real room/audio checkpoint. Live gates and a fresh managed-room join pass; keep open until the Pixel confirms join/audio over the tailnet.
