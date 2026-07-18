---
id: rad-v2mj
status: closed
deps: [rad-7h1e]
links: []
created: 2026-07-17T23:28:33Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-1wji
tags: [radcliffe, agent-control, settings, android]
---
# Let the agent update allowlisted Radcliffe settings

Expose a safe conversational path for changing registered Radcliffe behavior such as the default speaking pace, while keeping secrets and arbitrary application state outside model authority.

## Design

The agent requests a typed setting mutation through the action contract. A registry owns the setting schema, bounds, scope, persistence, confirmation and undo behavior. Android and the control plane apply accepted results and render the same effective value.

## Acceptance Criteria

A user can ask by voice to inspect, change, reset, or temporarily override each registered setting; every setting has a typed schema, bounds, allowed scopes, persistence rule, confirmation policy, and default; the UI reflects the effective value and offers undo or reset; model requests cannot read or change credentials, endpoints, capability grants, hidden developer options, arbitrary preference keys, or paid-provider policy; failures and conflicts are visible without silently changing state; changes survive or expire exactly according to scope; and tests cover synonyms, bounds, precedence, concurrent devices, replay, reconnect, corruption, redaction, and denied settings.


## Notes

**2026-07-18T05:43:29Z**

Implemented the closed allowlisted configuration registry, typed agent action lifecycle, control-plane and sidecar paths, runtime application, Android effective-value UI with reset/undo, scoped persistence/expiry, replay/conflict/corruption handling, and redacted audits. Full make check passes: 222 Python passed, 1 skipped; 29 sidecar tests; Android unit tests and lint.
