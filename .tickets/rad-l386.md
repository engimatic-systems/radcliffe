---
id: rad-l386
status: in_progress
deps: []
links: [rad-nhai, rad-ncdz, rad-uk3f, rad-8ai7, rad-8akg, rad-gi5i, rad-1w52]
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

**2026-07-21T15:00:04Z**

2026-07-21 isolated Haiku conformance job job_9df03620527e83b55c8f1e8fe7352ec successfully fetched the official Hacker News API data and wrote its report, then exposed an executor bug: DockerSandbox embedded model commands inside a brace group followed by a semicolon, which made valid heredoc terminators fail parsing. After Haiku worked around that syntax, the next provider exception was collapsed to executor_failed. The command is now passed as a separate shell argument, and bounded provider retries emit stable model_rate_limited, model_provider_unavailable, model_request_failed, or model_response_invalid codes with content-safe private diagnostics.

**2026-07-21T15:02:47Z**

2026-07-21 deployed regression pass on revision 90ab7fd: isolated Haiku job job_f627f6ffe8df4fb07bd40bd41e3ff8b7 fetched the official Hacker News Firebase API and succeeded at revision 8. It promoted two artifacts, one UTF-8 Markdown source, and one grounded finding; independent verification confirmed the source names the official API and contains at least ten headline rows. The conformance session was archived. Physical phone fetch/read remains the operator-final-check gate.

**2026-07-21T15:33:18Z**

2026-07-21 third physical trace ses_103f1841319850c7a20dfcff234d84ef: job_1a3258add04b7b942c9e51dedc1322c0 succeeded and wrote a 3,370-byte UTF-8 Markdown artifact, but declared zero sources, so read_job_source returned not found. The result reader now derives a hash-verified source capability from safe Markdown artifacts, including historical results, and new jobs automatically promote such artifacts. Exact hidden jobId scoping prevents collisions; the observed historical artifact now resolves locally as hacker_news_headlines.md.

**2026-07-21T15:54:25Z**

2026-07-21 final Pixel acceptance ses_0871ab859cecd63c47447826759d2c0e: job_ec6362e939637b66034f6b30b80efca1 downloaded Hacker News, extracted 30 headline-only rows, automatically promoted the 1,626-byte UTF-8 hacker_news_headlines.md source, and completed at revision 7. The next voice turn resolved it through a session/job-scoped capability, persisted canonical source bytes with matching SHA-256 and provenance, and read the full result aloud. There were no denied or repeated tool calls, and the operator explicitly confirmed this was the desired result.

**2026-07-25T16:31:26Z**

2026-07-25 physical regression ses_802b2b0bd8b7d1b0ce276b3d3e990dd8: Haiku selected read_job_source at least six times for succeeded job job_ae71375432f558547c819b90d4e97148, but every call returned not_found and the model fell back to conversational output. The promoted source exists at hn_headlines.md. Root cause: the prompt/tool contract advertises Markdown artifact filenames while _resolve_job_source matched only source names/entity IDs; manage_job also omitted the source path/artifact mapping from modelContent. Reopened pending a real foreground Haiku read_job_source acceptance.

**2026-07-25T16:35:37Z**

2026-07-25 live foreground acceptance on revision 1fcb732 passed with pinned claude-haiku-4-5-20251001 against the exact failed phone job. Given only the chronological succeeded-job outcome and natural request 'Please read the completed Hacker News headlines aloud,' Haiku's first and only tool was read_job_source with reference 'Hacker News headlines' plus the hidden exact job ID. The gateway returned a canonical source envelope with SHA-256 b24c944c55e6885269bb3f71e30f4d46750ace4d9cb3314e9e4dbf2beb49c3ff; model output was not accepted as evidence. Provider cost was about USD 0.005564. Ticket remains in progress for the operator's phone/TTS confirmation.
