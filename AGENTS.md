# Radcliffe agent instructions

Copy every Android APK produced for testing to `/Users/ravd/agent-share/radcliffe/`.

Before sharing any network service with the user, run the Tailscale CLI to inspect the current Serve configuration and choose an available permitted port.

Use the pinned Sonnet 5 profile (`claude-sonnet-5`) for every operator or user acceptance test,
including both the foreground conversational sidecar and the background job model. This replaced
the earlier Haiku 4.5 pin on 2026-07-25 at the user's direction: Haiku was not following the tool
contract well enough for the source-reading workflow. Treat Qwen as a deprecated, explicit
development-only path; do not use it as user-test evidence unless the user reverses this
instruction.

Sonnet 5 rejects non-default `temperature`, `top_p`, and `top_k` with a 400, and manual
`budget_tokens` thinking config with a 400. Adaptive thinking is on by default when the
`thinking` field is omitted. Do not reintroduce those parameters on the Anthropic request path.

## Conversation data

- Treat Radcliffe conversation text and stored conversation envelopes as available for agent
  inspection by default when useful for implementation, testing, or diagnosis.
- The user will explicitly identify content that should be hidden or excluded. Honor those
  instructions from that point forward.
- This permission does not make credentials, API keys, authentication material, or unrelated
  private host data non-secret. Do not print, commit, or place those values in model-visible tool
  results.
- Do not publish conversation content externally merely because local inspection is permitted.
  Sending conversation context to the model provider selected by the user is part of the normal
  configured application path; other disclosure still requires an in-scope reason or explicit
  authorization.
