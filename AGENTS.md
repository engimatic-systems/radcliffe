# Radcliffe agent instructions

Copy every Android APK produced for testing to `/Users/ravd/agent-share/radcliffe/`.

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
