---
id: rad-gi5i
status: closed
deps: []
links: [rad-l386]
created: 2026-07-21T15:19:35Z
type: feature
priority: 1
assignee: coopwilliams
tags: [radcliffe, background-agent, permissions, sources, dependencies, operator-autonomous]
---
# Stage explicitly authorized sources and prior job artifacts into dependent jobs

Disposable jobs cannot currently consume an authorized foreground source or an artifact from an earlier job, even when the user explicitly refers to that file. The observed follow-on Hacker News extraction therefore searched an empty workspace and failed source_unavailable.

## Design

Represent job inputs explicitly, validate every source or artifact against the same session and capability boundary, persist immutable hashes, and stage bounded read-only copies into a new attempt workspace. Never expose arbitrary host paths or mount another job workspace directly.

## Acceptance Criteria

run_job can receive explicit same-session source/artifact inputs; only validated bounded files are staged read-only with hashes and provenance; unauthorized, ambiguous, mutable, cross-session, and symlink inputs fail closed; a dependent extraction consumes a prior HTML artifact without redownloading it.


## Notes

**2026-07-21T15:33:18Z**

2026-07-21 trace evidence: job_2dcc24cc22436e52f2c074385cf3dc7b could not see the foreground-configured radcliffe-demo.md source, and job_24c11e7b04612ebfb92c4129241408d4 could not see the HTML artifact produced by the earlier successful job. Each job correctly had an isolated empty workspace, so the missing capability is explicit authorized input staging rather than a host permission relaxation. Kept open for a separate bounded capability design.

**2026-07-23T03:11:45Z**

Implemented explicit current-session source and prior succeeded-job artifact inputs for run_job. The control plane resolves references fail-closed, snapshots bounded bytes plus SHA-256/provenance in migration 013, retains them across retries/backups, and stages fresh read-only copies under /workspace/inputs without mounting source roots or producer workspaces. Sidecar and both executors expose only bounded metadata and direct agents to consume staged inputs without redownloading. Regressions cover same-session resolution, traversal, symlink, ambiguity, mutation, duplicate/cross-session/hash rejection, immutable persistence, read-only staging, and a dependent HTML title extraction with no network. Full make check passes: 372 Python passed/1 skipped, 107 sidecar tests, TypeScript, Android unit/lint, Ruff, formatting, and mypy.
