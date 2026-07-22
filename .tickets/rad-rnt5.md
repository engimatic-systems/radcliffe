---
id: rad-rnt5
status: closed
deps: []
links: []
created: 2026-07-22T02:30:39Z
type: feature
priority: 0
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, background-agent, search, budget, trafilatura, agent-autonomous]
---
# Add bounded Trafilatura previews for background web research

Background web research repeatedly prints large raw HTML responses into Haiku context, exhausting the existing per-attempt budget. Add a first-class page preview operation that downloads through the sandbox policy boundary, extracts article text with Trafilatura, persists full private source files, and returns only bounded relevance-oriented snippets.

## Design

Pin Trafilatura in the job image and control-plane environment. Download only bounded HTTP(S) HTML/text responses, extract clean text with metadata, select deterministic query-relevant plus stratified sentence windows, return at most five bounded chunks, and retain full HTML/text under the private attempt workspace. Keep the existing per-attempt dollar cap unchanged and persist actual token/cost usage in the private transcript.

## Acceptance Criteria

Tests cover URL and argument safety, output bounds, extraction and saved artifacts, untrusted labeling, opaque Docker argv, model-context isolation from raw HTML, and cost observability. A live sandbox smoke against a public page returns five or fewer chunks and usable saved text without increasing the configured budget.


## Notes

**2026-07-22T02:31:08Z**

Implemented pinned Trafilatura 2.1.0 extraction, bounded query-relevant/stratified previews, private full HTML/text artifacts, 8 KiB tool-result caps, and private per-response/cumulative Haiku cost accounting without raising the /bin/zsh.25 attempt cap. Python gates pass with 356 tests (1 skipped). A real Colima sandbox smoke fetched the public Abraham Lincoln Online Gettysburg page through the existing egress proxy, extracted 12,277 characters from 18,599 downloaded bytes, returned 5 preview chunks totaling 1,364 characters, saved both source files, and cleaned up.

**2026-07-22T02:34:11Z**

Deployment hardening: the first preflight run exposed that RADCLIFFE_JOB_SANDBOX_IMAGE and the image embedded in RADCLIFFE_JOB_EXECUTOR_COMMAND could drift while readiness still reported green. Runtime config was corrected before restart; the preflight now requires both values to match, with a deterministic stale-image regression. Full Python gates pass with 357 tests (1 skipped).
