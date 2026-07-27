# Milestone 1 acceptance

This is the reproducible closeout procedure and evidence index for the
single-Pixel Radcliffe foundation demo. It distinguishes deterministic local
conformance from physical user acceptance.

## Accepted configuration

- Android: the enrolled target Pixel running the native foreground voice
  service.
- Media: self-hosted LiveKit signaling through the trusted tailnet checkpoint,
  with the server advertising its tailnet node for direct RTC.
- Foreground model: the persistent `claude-sonnet-5` user-test profile.
- Speech recognition: NVIDIA Parakeet TDT 0.6B v3 through MLX.
- Speech synthesis: Kokoro `bf_emma`.
- Source tool: capability-scoped `read_source` over
  `fixtures/sources/radcliffe-demo.md`.

Public TURN-only operation, network transitions, Bluetooth routing, and the
broader device matrix are intentionally post-M1 reliability work in
`rad-g9yr`; they are not claimed by this tailnet acceptance.

## Evidence

| Requirement | Evidence and practical result |
| --- | --- |
| Pixel and worker connectivity | Repeated enrolled Pixel sessions reached the self-hosted LiveKit room through trusted tailnet signaling while supervised workers connected outbound. `rad-8i08` records the inspected Serve topology and the explicit TURN deferral. |
| Ordinary answer under five seconds | The Sonnet 5 physical run at revision `da0027b` in session `ses_c3ef928b38a19c18d656c8d145d6986c` produced nine consecutive first-audio measurements under five seconds: 3.908, 4.501, 3.984, 4.137, 4.087, 4.780, 3.482, 3.928, and 3.426 seconds. |
| Speech interruption | The heard-output suite measured verified-speech stop at 402 ms, under the 500 ms gate. Physical Pixel runs in the accepted playback/interruption tickets confirm that stale queued audio stops and replacement turns remain audible. |
| Exact source and provenance | The independent voice report at `artifacts/voice-conformance/20260721T042015Z/report.json` records the 252-byte canonical fixture, SHA-256 `1882c547e099cae37966f860e73d9a11e59ba3951ce611280ccfdb90aebbf302`, the typed `read_source` lifecycle, READING state, and exact TTS bypass. |
| Persisted pause and resume | The same report records an 8 ms pause acknowledgement, heard audio stopped at 47 ms, and audio resumed from the persisted boundary in 535 ms. Physical session `ses_d94bbaad38276fc8a0c5076cfc6e2e9f` confirms app/media-control playback with no completed-sentence replay. |
| End, restart, and reconnect | Physical acceptance under `rad-mp26` created, ended, resumed, and switched server-generated conversations without losing their timelines. Session and worker conformance cover restart idempotency and orphan prevention. |
| Long pauses preserve user speech | Physical Sonnet 5 session `ses_ef2e10ab50a9d4193cb9e74c35d6fa90` persisted three interrupted user messages exactly once and in order. The model correctly recalled all three plus the opening preamble; `rad-wbza` and `rad-0mws` retain the packet and timing evidence. |

The local report uses deterministic/local providers to prove transport,
playback, source, and audit contracts. It is not cited as Qwen user-acceptance
evidence. Model behavior and the final phone observations above come from the
pinned Sonnet 5 profile.

## Reproduce the gate

1. Run the deterministic repository and conformance gates:

   ```sh
   make check
   make check-conformance-local
   ```

2. Inspect the current Tailscale Serve allocation before starting or sharing
   the phone services. Install and verify the pinned user-test profile:

   ```sh
   make services-install-user-test
   make services-status-local
   make check-background-jobs-local
   ```

3. On the Pixel, create a new conversation and wait for Listening. Confirm the
   worker has already subscribed to and processed microphone input.
4. While the app shows Thinking and before assistant audio starts, say these
   three utterances in sequence: “Buffer message one: Alpha,” “Buffer message
   two: Bravo,” and “Buffer message three: Charlie.” Then ask it to list the
   three marker words once each and in order. Confirm it answers Alpha, Bravo,
   Charlie without dropping or duplicating a message.
5. Say: “Start a background job that writes a short Markdown file containing
   the first ten Fibonacci numbers.” Confirm the immediate acknowledgement is
   one short sentence without internal IDs, policy, budget, or implementation
   detail, and that the completion arrives independently.
6. Ask an ordinary question and confirm meaningful speech begins within five
   seconds.
7. Ask for a long answer, then interrupt it by speaking. Confirm old audio
   stops within 500 ms and the replacement turn is accepted.
8. Ask: “Read the authorized source `radcliffe-demo.md` exactly and slowly.”
   Confirm the source presentation shows provenance and the canonical text is
   read rather than regenerated.
9. Pause during a sentence using an app or system media control, then resume.
   Confirm completed sentences do not replay and reading continues from the
   persisted boundary.
10. End the conversation, reopen it, and create or switch to another
    conversation. Confirm no stale audio, model work, or tool operation appears.
11. Preserve the session ID. Review its envelopes, playback ledger, tool audit,
    content-free phase telemetry, and the newly generated conformance report
    against the evidence table above.
