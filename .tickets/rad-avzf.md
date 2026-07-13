---
id: rad-avzf
status: open
deps: []
links: []
created: 2026-07-13T01:37:25Z
type: task
priority: 0
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, foundation, security, milestone-1]
---
# Define Radcliffe capability policy and threat model

Turn the spec security model into an actionable policy for tools, filesystem access, source handling, confirmations, web fetches, credentials, and retention.

## Acceptance Criteria

Threat model covers prompt injection, path traversal, symlink escape, SSRF, malicious archives, stale tokens, tool replay, and data retention; capability-grant format and confirmation matrix are specified; every initial tool has an allowlist, input constraints, side-effect classification, retry policy, and audit requirements; security test cases are enumerated.
