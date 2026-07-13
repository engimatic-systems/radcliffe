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
- optional `RADCLIFFE_SIDECAR_PORT` (default `8787`, loopback only)

`PiAgentOrchestrator` is the replacement seam. Another loop can implement the
same `run(TurnRequest, emit, signal)` contract without changing envelopes,
projection policy, tools, Android, or playback state.
