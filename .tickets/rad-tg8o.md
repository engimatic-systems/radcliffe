---
id: rad-tg8o
status: closed
deps: []
links: [rad-erac, rad-7t9h, rad-orob]
created: 2026-07-17T23:54:20Z
type: epic
priority: 1
assignee: coopwilliams
tags: [radcliffe, agent, unix, sandbox, web, data-analysis]
---
# Provide Unix-capable agents inside a user-supplied sandbox

Let Radcliffe perform broad web, download, coding, and data-analysis workflows by giving a job agent normal Unix tools and network access inside a containment boundary selected and owned by the user.

## Design

Make the deployment boundary, not a maze of per-command prompts, the primary safety boundary. A host-trust profile may grant the worker unrestricted shell authority inside a disposable VM, container, separate OS account, or intentionally disposable host. Keep low-level job turns out of the voice conversation by exposing one run_job lifecycle to the conversational agent and retaining the worker transcript separately. Broker only explicitly granted credentials and mounts into the worker.

## Acceptance Criteria

The user can select a documented restricted profile or an advanced host-trust sandbox profile; in host-trust mode the job agent can execute arbitrary Unix commands, create and modify files, install workspace-local dependencies, access the user-authorized internet, download bounded data, and run long analyses without per-command confirmation; the conversational model invokes one idempotent run_job operation and receives bounded progress plus final summaries and artifact references rather than every shell call; the job has a separate replayable context and workspace; cancellation, timeout, disk, CPU, process, and output limits are enforceable by the chosen sandbox; Radcliffe service credentials, host keychains, GitHub credentials, LAN access, and unrelated mounts are absent unless explicitly delegated; prompt-injected downloaded content cannot escape the sandbox boundary; artifacts and provenance are durable and inspectable; and conformance covers web acquisition, multi-step analysis, code execution, large output compaction, restart/resume, cancellation, attempted secret access, and network-boundary escape.


## Notes

**2026-07-18T00:47:41Z**

2026-07-17 target workflow: during an hour-long hands-free drive, the foreground agent can launch a six-month lab-paper search, keep chatting or remain quietly listening, announce completion safely, resolve a spoken reference to one returned paper, read its abstract exactly, search the full paper for applications, then launch a sandboxed website/TAM analysis while the conversation stays responsive. Background tool transcripts remain separate; only selected findings, sources, calculations, and artifacts are promoted.

**2026-07-20T06:04:58Z**

Acceptance complete across closed children rad-erac and rad-nhai plus linked rad-7t9h. The documented opt-in Colima/Docker restricted profile uses a pinned image, credential-free local broker, disposable per-job workspace, process/runtime/output/filesystem limits, no unrelated mounts, and default-deny public-only proxy. Deterministic and deployed evidence covers Unix code/data workflows, workspace-local packages, web acquisition, large-output bounds, separate replayable context, restart/lease recovery, follow-up/cancellation, secret/host probes, private-network/metadata/redirect/DNS escape attempts, and cleanup. Deployed 75d8bae readiness is green; artifacts/background-job-findings/20260720T055825Z/report.json proves public acquisition, durable exact source plus source-bound findings and inference labels; artifacts/voice-conformance/20260720T060147Z/report.json proves the responsive voice/source/interruption path. Private command transcripts remain separate and only bounded summaries, artifacts, source entities, and findings are promoted.
