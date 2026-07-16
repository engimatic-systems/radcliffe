# Radcliffe conformance suite

The conformance suite drives Radcliffe as an external client and evaluates
what was actually heard. It borrows useful behavioral scenarios from Oxford
without copying Oxford's transport or state architecture.

| Scenario | Radcliffe authority | Automated evidence |
| --- | --- | --- |
| Normal multi-turn conversation | Protocol v2 messages plus captured audio | `make check-voice-local` |
| Short natural pause | One endpoint and one transcription | STT corpus and endpoint unit test |
| Long clause pause | Newest clause owns the session; no overlapping answer | Live acoustic replacement and endpoint unit test |
| Tap interruption | Audible silence and state acknowledgement | Live acoustic run |
| Speech barge-in | Old audio stops; old HTTP stream closes; replacement wins | Live acoustic run |
| Pause/resume | Paused microphone creates no envelope; source audio flushes and resumes from the SQLite sentence cursor | Live acoustic run plus playback ledger |
| Background noise, user-path echo, and frame loss | Seeded waveform degradation and STT thresholds | STT corpus |
| Speaker playback echo | No extra user envelope on the Pixel communication-audio path | Physical run plus envelope source sequence |
| Session restart and recent ordering | Durable catalog and restored timeline | Isolated black-box process restart plus Control/Android tests |
| Context accounting | Exact provider payload and public content-free metric | Sidecar/control golden tests |
| Exact source reading | Canonical bytes, provenance, model bypass, and resumable replay | Domain/pipeline contracts plus live isolated-store tool run |
| Provider/TTS failure recovery | Failed disposition and next-turn recovery | Deterministic pipeline fakes |

Generated audio, transcripts, envelopes, state events, phase timings, and the
agent-audio timeline are retained under `artifacts/`. Synthetic provider runs
and real local Ollama/Whisper/TTS runs are labeled separately. The suite uses
local providers by default and does not require GitHub Actions.

`make check-conformance-local` runs the complete local release loop: the
isolated session/restart suite, false-turn rejection corpus, production STT
profile, and real LiveKit/Whisper/Ollama/TTS voice suite. The faster
`make check-session-local` target starts a control-plane process on an unused
loopback port with its own generated source root and SQLite store, restarts the
process in place, and retains a redacted HTTP timeline plus durable database.

Oxford scenarios that require unrestricted filesystem writes or agent-driven
self-reconfiguration are deliberately not copied: Radcliffe's current
capability contract is read-only and settings changes remain user-authorized.
The frontier-backend reasoning probe is also excluded from automatic runs
until the user explicitly enables a bounded cloud-provider test. These are
product-policy differences, not gaps hidden by the harness.
