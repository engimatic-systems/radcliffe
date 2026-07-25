---
id: rad-erac
status: closed
deps: [rad-7t9h]
links: [rad-7t9h, rad-tg8o, rad-orob]
created: 2026-07-19T20:51:44Z
type: feature
priority: 0
assignee: coopwilliams
parent: rad-tg8o
tags: [radcliffe, agent, unix, docker, ollama, sandbox, deployment]
---
# Deploy a Colima-backed Unix background agent

Provide the concrete user-owned Colima/Docker wrapper needed for Radcliffe background research, coding, download, and analysis jobs, then deploy it to the protected local profile.

## Design

Keep orchestration and local-model inference in a credential-free host wrapper while every model-selected Unix command executes in a disposable Linux container with only the per-job workspace mounted. Use the local Ollama endpoint without provider credentials; apply explicit resource, process, filesystem, and network limits; translate only allowlisted lifecycle/result references into the existing JSON-lines executor contract; retain private reasoning and command output only in the mode-0600 executor transcript/workspace.

## Acceptance Criteria

A protected local profile can opt into a pinned Colima/Docker image and start a job from voice; the wrapper supports iterative local-Ollama planning, arbitrary shell commands inside the container, bounded public-internet acquisition, workspace-local package installation, durable report/artifact output, follow-ups, cancellation, and timeout; it mounts no host path except the private attempt workspace and receives no Radcliffe/provider/GitHub/keychain credentials; private/LAN endpoints and host metadata are blocked while required local inference is brokered outside the container; deterministic tests cover coding, public-web analysis, malformed model output, command/output/resource limits, secret and host-path probes, and container cleanup; an end-to-end deployed smoke proves the foreground remains responsive and Android sees lifecycle completion.

## Notes

**2026-07-19T21:28:19Z**

Deployed the revision-tagged Colima image and protected local executor profile. Real local-Ollama/Colima jobs completed through the live control-plane lease path, promoted bounded results, kept a concurrent foreground tool responsive, and cleaned up containers/networks; private/LAN, host-path, credential, malformed-output, resource, and artifact boundaries are covered. Physical Android lifecycle confirmation remains the checkpoint.

**2026-07-20T00:13:19Z**

Revision ef59f64 fixes the phone voice route discovered at deployment: LiveKit now advertises the verified tailnet address on the selected RTC port and room credentials use the tailnet WSS origin. Operations, background-job, and fresh managed-worker room checks pass; Android lifecycle confirmation remains pending.

**2026-07-20T04:50:03Z**

Acceptance completed across the existing implementation and the autonomous follow-up chain. The revision-pinned protected profile passes capability/executor/image/synthetic readiness. A deployed local-Ollama/Colima job used proxied public HTTPS, verified a fetched document, promoted report.md, surfaced lifecycle through the enrolled-device API, and cleaned every container/network; the content-free egress smoke also passed curl, wget, HTTPS Git, Python, pip, npm, direct-route denial, RFC1918, tailnet, and metadata cases. A second real job was cancelled during a shell step and reached durable cancelled without late success or leftover resources. Deterministic suites cover coding, artifacts, malformed/batched model calls, resource/output/runtime limits, follow-ups, cancellation, host paths, credentials, private networks, and foreground API responsiveness. The handoff records later successful phone evidence, and children rad-7t9h, rad-rmfz, and rad-fdtf are closed.
