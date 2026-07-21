---
id: rad-l386
status: in_progress
deps: []
links: [rad-nhai, rad-ncdz, rad-uk3f, rad-8ai7]
created: 2026-07-21T14:12:34Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-nhai
tags: [radcliffe, background-agent, sources, tools, voice, regression, operator-final-check]
---
# Make downloaded job results readable from the foreground conversation

A physical Pixel voice session asked Radcliffe to fetch the front page of a website and read its headlines. The foreground model first called read_source for a path that did not yet exist, then started a background job. The job downloaded the page successfully as a generic artifact, but promoted no source entity. On follow-up, the foreground model repeatedly sent that job-workspace-relative artifact path to read_source, whose separate source-root capability denied every call, so the downloaded content could not be read.

## Design

Preserve the capability boundary between configured read_source roots and private job workspaces. When a user asks a background job to fetch material for later reading, make the job request promote an exact source entity and teach the foreground model to resolve it through read_job_source. For a completed generic file artifact, provide a grounded recovery path (for example, a follow-up/retry that promotes the artifact when safe) instead of retrying read_source or claiming the workspace path is readable. Keep canonical bytes out of model context when exact rendering is requested.

## Acceptance Criteria

A deterministic replay of the observed fetch-then-read conversation never sends a job artifact path to read_source; the job either returns a promoted source entity or the foreground offers/executes a bounded source-promotion recovery; an exact follow-up uses read_job_source and reaches canonical rendering with audited provenance; denied tool calls include a grounded private reason and do not trigger repeated identical calls; local Qwen and Haiku profile tests cover the routing; an end-to-end Pixel retry can download a public Markdown page and hear its requested contents.


## Notes

**2026-07-21T14:12:42Z**

2026-07-21 physical trace: session ses_e2bc076a0ba13adc153e659d12741bf6, job job_3c60a3fcbf14ecdb0118f06571908039. The job succeeded on attempt 1 and promoted a 5,430-byte artifact at marginalrevolution.com/front_page.md, but entity_ids_json was empty and promoted-result sources/findings were empty. Four read_source calls (call_vt52j0eo, call_cfntwn1s, call_kra9u1u1, call_e3c0mbo8) were authorization=denied/outcome=denied before execution. The model first claimed the download path was ready, then retried the wrong tool three times before finally attributing the failure to permissions. This is a foreground/job result-contract failure; switching models may improve recovery but cannot grant read_source access to a private job artifact.

**2026-07-21T14:15:02Z**

Operational follow-up 2026-07-21: restarted the installed sidecar/control/worker services with the pinned live Haiku profile (anthropic/claude-haiku-4-5-20251001). Content-free readiness is green, the original phone session remains active with its managed worker running, and two bounded provider requests completed with zero held budget. The diagnostic request returned the requested smoke phrase but omitted only its final period; provider integration, usage accounting, and stream completion all succeeded. The fetch/read acceptance itself remains open for the operator retry.

**2026-07-21T14:51:53Z**

2026-07-21 second physical trace ses_ece6459346fbe944ae5be96736984b1c exposed two additional causes. The foreground sidecar was Haiku, but the background executor was still Qwen. Its Hacker News attempt used an obsolete HTML selector, then malformed or incomplete API pipelines, repeated identical failures, emitted eight no-action responses, and exhausted 24 steps. The Marginal Revolution attempt also called finish_job with an explicitly failed summary, incorrectly producing succeeded status. The fix must pin Haiku for the job broker, block identical failed commands/no-action loops, and provide a typed unsuccessful completion with a durable summary.
