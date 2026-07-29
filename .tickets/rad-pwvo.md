---
id: rad-pwvo
status: open
deps: []
links: [rad-hkvx]
created: 2026-07-26T03:53:30Z
type: bug
priority: 1
assignee: coopwilliams
tags: [radcliffe, tools, jobs, sources, discovery, post-m1]
---
# Promoted job artifacts are readable but not discoverable, so the model denies they exist

A completed research job promoted five artifacts (a summary report plus four verbatim Wikipedia sources). The operator could have the report read aloud but could not get any verbatim article read. Asked for them, the top-level model reported that it had no title or path to reach them.

Worse than the missing capability: the model actively denied the artifacts existed. It called list_sources, received only the two session fixture files, treated that as the authoritative catalog, and told the operator the job had produced a single artifact rather than the number they remembered. The operator was correct and had to insist twice before the model checked the job itself, at which point manage_job status returned all five sources with their human titles.

## Design

ROOT CAUSE - list_sources and read_job_source address disjoint namespaces, and nothing communicates that.

The session grant is built with readable_roots=(settings.source_root.resolve(),) and allowed_tools={list_sources, read_source} (app.py, session response construction). list_sources enumerates exactly grant.readable_roots (tools.py). Job artifacts never live under that root: they live under var/jobs/<job>/attempt-N/files/, reachable only via read_job_source, which mints a separate short-lived grant scoped to that one promoted source's root with allowed_tools={read_source} and then executes read_source beneath it. That indirection is also why the tool_calls audit shows read_source rows resolving into var/jobs/... - those were read_job_source calls.

Evidence from the tool audit for the reported session:
- read_source resolved to /Users/.../var/jobs/job_258e.../attempt-1/files/civic_mini_cities_summary.md and succeeded, so job artifacts ARE readable when a reference is known.
- list_sources in the same session recorded its enumerated roots as ["/Users/.../fixtures/sources"] and returned two fixture filenames. The job workspaces were absent from its grant entirely.

Promotion itself is healthy and is NOT the bug. Running read_promoted_sources against the job returns all five sources with names, locators, and paths, and promoted-result.json contains a well-formed sources array. manage_job status returns them correctly; the model recited all five accurately once it called it.

So the defect is discovery, plus a false authority signal. list_sources presents as "the sources you can read" while actually being "the sources in the session fixture root". A model reasonably treats an empty result as proof of absence, which is how a factual question about the operator's own completed job got answered wrongly and confidently.

FIX DIRECTIONS - decide before implementing:

1. Preferred: let list_sources report promoted job sources as a distinct, labelled group, resolved through the same read_promoted_sources path manage_job status already uses. Discovery is not read authority; enumerating a promoted source's title need not hand out standing read access to job workspaces, and read_job_source can keep minting the narrow per-source grant it does today.

2. Regardless of 1, scope the result so it cannot be read as universal. list_sources should state that it covers session sources and that background job artifacts are enumerated through manage_job status. This alone prevents the confident false denial, which is the most damaging part of the bug.

3. Do NOT simply widen the session grant's readable_roots to include every job workspace. That trades a discovery bug for standing ambient authority over all job output and undermines the per-source grant model.

4. Tool selection needs a look too. After manage_job status had given it the human titles, the model was asked to read one by name and called list_sources rather than read_job_source, whose description already accepts a human title. Once it was denied it asked the operator for a title it had just recited. Worth tightening the wording so a named job artifact routes to read_job_source directly.

RELATED: the list_sources call that failed at this point was denied outright, one interruption after an identical call succeeded. rad-hkvx already tracks denials on promoted job paths; this session adds a list_sources instance and shows the denial arriving after a barge-in. Fixing discovery will not fix that, and the two together are what made the feature unusable.

## Acceptance Criteria

After a background job completes with promoted sources, the operator can ask for any promoted artifact by its human title and hear it read, without supplying a filename and without having to tell the model to check the job first. The model never asserts that a promoted artifact does not exist on the strength of a list_sources result: either that result includes promoted job sources, or it is explicitly scoped so absence from it is not evidence of absence. Asked to read a named job artifact, the model routes to read_job_source rather than list_sources. Promoted sources remain reachable only through appropriately scoped grants, with no standing read access to job workspaces added to the session grant. A regression test builds a completed job with promoted sources and asserts every one is discoverable by the top-level agent and readable by title. Verified by a Pixel run: complete a multi-source research job, then ask for one of its verbatim articles by name and hear it read.
