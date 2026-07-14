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

`PiAgentOrchestrator` is the replacement seam. Another loop can implement the
same `run(TurnRequest, emit, signal)` contract without changing envelopes,
projection policy, tools, Android, or playback state.

`GET /healthz` exposes content-free `active_turns` and cumulative
`cancelled_turns` counters. The external conformance suite uses them to prove
that speech replacement closes the abandoned HTTP stream; neither prompts nor
model output are included.
