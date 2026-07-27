---
id: rad-orob
status: closed
deps: []
links: [rad-erac, rad-7t9h, rad-u96k, rad-e9wi, rad-rnt5, rad-tg8o, rad-wobn, rad-1w52]
created: 2026-07-25T15:19:17Z
type: feature
priority: 0
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, background-agent, claude-agent-sdk, sandbox, unix, artifacts, operator-final-check]
---
# Add sandboxed CLI execution and atomic path-only Markdown publishing to Agent SDK jobs

Agent SDK jobs cannot currently transform downloaded files with ordinary CLI tools. Requiring document contents in read_artifact or write_markdown routes large user artifacts through model context and still permits ordinary model text to end a job without publishing anything.

## Design

Expose a custom MCP run_command tool backed only by DockerSandbox.execute; do not enable native host Bash, Read, or Write. Commands operate on workspace paths and return bounded diagnostics while large content stays in files. Add publish_markdown(path, metadata) that validates an existing relative Markdown file, promotes it, records result metadata, and succeeds atomically without accepting document content. If an SDK response ends without publish_markdown, finish_job, or fail_job, issue at most two corrective Agent SDK turns before failing model_no_action.

## Acceptance Criteria

Agent SDK Haiku can download HTML, transform it with sandboxed CLI tools, and publish a Markdown path without document bytes crossing a model tool call; traversal and missing paths are rejected; native host tools remain unavailable; publish_markdown is terminal and atomic; two corrective turns are attempted after non-terminal replies, then model_no_action is deterministic; integration tests reproduce the recent Hacker News failure and pass.


## Notes

**2026-07-25T15:36:40Z**

2026-07-25 implementation is locally green. Agent SDK exposes custom run_command backed by the credential-free DockerSandbox and keeps native Bash/Read/Write disallowed. write_markdown was removed from the SDK tool surface. publish_markdown accepts only an existing UTF-8 Markdown path plus metadata and atomically promotes/finishes it. Normal non-terminal replies receive at most two corrective turns. Docker egress/isolation smoke passed all public-network and denial cases.

**2026-07-25T15:39:45Z**

2026-07-25 live Haiku regression found the SDK turn ceiling stopped immediately after Haiku emitted publish_markdown: eight tools had run, the requested Markdown existed, ResultMessage reported error_max_turns at turn 9, and total cost was only $0.02993225. Removed the default max_turns ceiling so the accepted five-minute runtime and $0.25 per-job spend cap bound complexity; --max-turns remains an optional operator override.

**2026-07-25T15:41:12Z**

2026-07-25 pinned-Haiku integration passed after removing the default turn ceiling. The exact Hacker News regression completed in 12 SDK turns at $0.04050375: download_page persisted the page, eight Docker run_command calls transformed/verified it, publish_markdown ran once, and the promoted artifact contained exactly three bullet lines with zero other non-empty lines. No corrective turn was needed; native host tools remained disabled.

**2026-07-27T04:13:17Z**

PR #63 is merged. Sandboxed run_command, path-only atomic Markdown publication, bounded corrective turns, staged inputs, and operator-owned runtime/budget policy passed the final integrated repository gate. The operator authorized closure.
