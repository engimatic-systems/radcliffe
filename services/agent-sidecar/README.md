# Radcliffe Pi sidecar

The sidecar is a stateless adapter around pinned Pi Agent Core `0.80.6`.
`POST /v1/turns` receives the complete Radcliffe `ContextPacket` projection for
a generation and streams newline-delimited lifecycle events. It is safe to
restart because SQLite and conversation state remain in the Python owner; the
next request reconstructs Pi context from its packets.

Pi is configured for sequential execution. Its `read_source` implementation
can only POST a typed request to `RADCLIFFE_TOOL_GATEWAY_URL`; it cannot access
the filesystem itself. Aborting the HTTP request propagates an `AbortSignal`
through Pi to that gateway call.

Required production environment:

- `RADCLIFFE_MODEL_PROVIDER` and `RADCLIFFE_MODEL_ID`
- the provider credential expected by Pi (for example `OPENAI_API_KEY`)
- `RADCLIFFE_TOOL_GATEWAY_URL`
- `RADCLIFFE_INTERNAL_TOKEN`, shared only with the loopback Tool Gateway
- optional `RADCLIFFE_SIDECAR_PORT` (default `8787`, loopback only)

The sidecar defaults to Ollama and rejects every non-local provider unless
`RADCLIFFE_ALLOW_CLOUD_MODELS=1` is set explicitly for that process. Keeping a
funded credential in `var/.env` therefore does not enable billable calls. Set
the opt-in only for a user-approved, bounded cloud-provider acceptance run.

The launch profiles make the boundary explicit. `scripts/run-local-sidecar.sh`
forces Ollama and removes cloud credentials from the child environment.
`scripts/run-live-sidecar.sh` requires an owner-only environment file, the
cloud opt-in, and `RADCLIFFE_ANTHROPIC_API_KEY`; it pins
`claude-sonnet-5`, bridges the key to `ANTHROPIC_API_KEY`, removes the
original key name, and discloses that projected conversation context is sent to
Anthropic. Node captures the key in the provider client closure and immediately
removes it from its environment so later child processes cannot inherit it.
Neither profile falls back to the other.

For the private local profile, set `RADCLIFFE_MODEL_PROVIDER=ollama`,
`RADCLIFFE_MODEL_ID` to an installed model, and optionally
`RADCLIFFE_OLLAMA_URL` (default `http://127.0.0.1:11434/v1`). Pi uses Ollama's
OpenAI-compatible streaming and tool-call endpoint with the ignored local key
`ollama`; no provider credential or prompt leaves the Mac.

Before reporting healthy, the sidecar loads the selected Ollama model through
the loopback native API. `RADCLIFFE_OLLAMA_KEEP_ALIVE` controls residency and
defaults to `-1` (retain until Ollama stops), preventing an idle model unload
from turning the next voice response into a cold-start latency failure.

The local model contract defaults to a 40,960-token context window with 8,192
tokens reserved for output. Override those matched model/runtime values with
`RADCLIFFE_MODEL_CONTEXT_LIMIT` and
`RADCLIFFE_MODEL_RESERVED_OUTPUT_TOKENS` only as a pair validated for the
selected Ollama model.

`POST /v1/context-usage` captures the exact Pi chat-completion payload and asks
the loopback Ollama endpoint for its authoritative `prompt_tokens` count using
a one-token, non-tool-executing dry run. Counts are cached by a SHA-256 digest
of the model-visible request. Normal turns perform the same cached preflight
and return an actionable 422 before real generation if projected input plus
reserved output exceeds the limit. If Ollama reports or silently truncates an
already-oversized prompt, the result is explicitly labeled an inexact lower
bound and remains an overflow; it is never presented as an exact count.

For the Sonnet live cloud profile, the same endpoint submits the structured Pi
payload to Anthropic's free token-count API. Anthropic documents that count as
an estimate, so Radcliffe adds 2% plus 32 tokens, sets `exact=false`, and labels
the measurement basis. The pinned model supplies the 200,000-token context
contract. `RADCLIFFE_CLOUD_MAX_OUTPUT_TOKENS` defaults to 1,024 and applies
across every provider sampling call in one foreground turn.

The pinned ID, context contract, and $1/$5 per-million input/output rates come
from Anthropic's [model overview](https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-5);
the estimate semantics come from its
[token-counting contract](https://platform.claude.com/docs/en/build-with-claude/token-counting).

Foreground cloud conversations have no accumulated dollar ceiling. Context
preflight, the per-call output limit, and content-free usage events remain in
force, but a long conversation is not disabled merely because earlier turns
cost more than a fixed session allowance. Dollar caps belong to individual
background jobs, whose executor enforces them independently.

`PiAgentOrchestrator` is the replacement seam. Another loop can implement the
same `run(TurnRequest, emit, signal)` contract without changing envelopes,
projection policy, tools, Android, or playback state.

Conversational `model_delta` events preserve provider text in `text` and may
add `spokenText` plus content-free `spokenCategories` when machine-oriented
detail needs a listening-friendly alternative. The media worker persists
`text` for the timeline and sends `spokenText` to TTS. Exact-output turns omit
the alternative, and canonical source envelopes bypass model deltas entirely.

`GET /healthz` exposes the provider, pinned model, local/cloud boundary,
accounting basis, the absence of foreground budget enforcement, and content-free
`active_turns` and cumulative `cancelled_turns` counters. The external
conformance suite uses the counters to prove that speech replacement closes the
abandoned HTTP stream; prompts, model output, credentials, and raw session IDs
are never included.
