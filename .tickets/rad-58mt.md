---
id: rad-58mt
status: open
deps: []
links: []
created: 2026-07-13T01:37:25Z
type: task
priority: 0
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, devex, milestone-1]
---
# Create reproducible Radcliffe repository and CI baseline

Establish the monorepo layout, pinned toolchains, Python and Android dependency locks, local development commands, linting, formatting, test entry points, and CI.

## Acceptance Criteria

A clean machine can bootstrap the Python, Node, and Android components from documented commands; Python uses a uv lockfile and Android uses a Gradle version catalog; CI runs formatting, static checks, unit tests, and schema generation; secrets are excluded from source control; all work is reproducible without the Oxford virtual environment.
