---
id: rad-bg6f
status: closed
deps: []
links: []
created: 2026-07-17T02:36:40Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-3se2
tags: [radcliffe, catalog, conformance, android]
---
# Keep conformance artifacts out of the user conversation catalog

A failed local voice conformance cleanup left an active zero-envelope radcliffe-conformance session in the same principal catalog. The Pixel opened it as a normal recent conversation, making a valid timeline appear intermittently blank.

## Acceptance Criteria

Conformance cleanup archives its session even when scenario execution or cleanup fails; retained test runs never appear in the default device catalog; existing leaked artifacts are remediated; and a regression test proves failure cleanup plus the normal recent-session timeline remain distinguishable and faithful.


## Notes

**2026-07-17T02:44:32Z**

Control logs proved the blank Pixel view requested leaked zero-envelope session radcliffe-conformance-6eb0e4ed43c8, while the next selection requested the real 18-envelope session. The stale artifact is archived and absent from the default catalog. Voice cleanup now isolates room, worker, and archive failures and verifies every archived run is hidden; external evidence is artifacts/voice-conformance/20260717T024055Z/report.json.
