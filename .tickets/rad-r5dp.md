---
id: rad-r5dp
status: open
deps: []
links: []
created: 2026-07-13T03:19:33Z
type: chore
priority: 3
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, ci, optional, post-milestone-1]
---
# Add optional GitHub Actions CI after repository sponsorship is approved

Restore an opt-in GitHub Actions workflow for Python, Node, protocol, and Android checks when organization billing and workflow permissions are available. Local make check remains the required gate until then.

## Acceptance Criteria

A least-privilege workflow runs make check or equivalent on pull requests; concurrency and cache settings limit spend; organization owners approve billing and permissions; the local gate remains documented.
