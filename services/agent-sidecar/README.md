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

`PiAgentOrchestrator` is the replacement seam. Another loop can implement the
same `run(TurnRequest, emit, signal)` contract without changing envelopes,
projection policy, tools, Android, or playback state.
