# Bounded playback protocol and buffer report

Status: implementation and automated acceptance complete; final physical Pixel
speaker/Bluetooth acceptance remains operator-gated.

This document is the measurement report for `rad-fnho`, protocol RFC and
refinement argument for `rad-pm6h`, and implementation guide for the remaining
`rad-0tmq` children. It supersedes the temporary-reconnect-only scope in
`bounded-playback-reconnect.md`.

## Result

The receiver window is one admitted nonterminal attempt (`W = 1`). A segment is
at most 30 seconds (`D = 30,000 ms`), so admitted but unheard audio is bounded by
`W * D = 30 seconds`. Normal model chunks are much shorter; the cap is a safety
limit, not a target. Oversized deterministic-source boundaries are divided into
parts without changing their logical resume boundary.

The sender admission seam is the durable ledger write immediately before the
reliable `PlaybackSegment` announcement. Realtime media publication is a second
transition and is forbidden until Android returns `PLAYBACK_STATUS_ACCEPTED`.
Credit returns only on Android decoded-PCM terminal evidence or an explicit
flush/failure transition. Synthesis, publication, elapsed time, and LiveKit
connection state never return credit or advance heard state.

## End-to-end buffer map

```text
model/source text
  -> [worker canonical segment queue: one unsynthesized item]
  -> [worker synthesized lookahead: one local item, not admitted]
  -> [durable admitted-attempt ledger: W=1, PCM <= D]
  -> PlaybackSegment --reliable data--> [Android accepted attempt: W=1]
  <- ACCEPTED -------reliable data-----/
  -> [Pipecat OutputAudioRawFrame: one awaited frame]
  -> [LiveKit native outbound audio/jitter/network: transport-owned, opaque]
  -> [Android RemoteAudioTrack callback: transport-owned, opaque]
  -> [DevicePlaybackTracker: one correlated decoded-PCM attempt]
  -> platform audio route
```

| Location | Owner | Bound | Observable | Controllable |
| --- | --- | --- | --- | --- |
| Canonical model queue | worker | one queued plus one active item | segment IDs and queue tasks | cancellation discards it |
| Deterministic-source prefetch | worker | configured depth, currently two text boundaries | source/boundary IDs | prefetch depth and cancellation |
| Synthesized model lookahead | worker | exactly one local segment | synthesis timing and duration | local only; cannot consume credit |
| Durable admitted ledger | worker/EventStore | one nonterminal attempt, PCM at most 30 s | complete content-free identity/status gauge | admission transaction and terminal update |
| Pipecat output frame | worker/Pipecat | one awaited frame per admitted attempt | enqueue/publish return timing | flush; no physical-playout authority |
| LiveKit outbound/native buffer | LiveKit/WebRTC | implementation-owned, no product duration API | connection/track callbacks only | flush/track lifecycle, not exact queue depth |
| Network and jitter buffer | LiveKit/WebRTC | adaptive and opaque | coarse transport events | disconnect/reconnect only |
| Reliable data delivery | LiveKit | ordered within the reliable channel, not ordered against media | message receipt | metadata acceptance barrier |
| Android track subscription | LiveKit Android | implementation-owned | subscribe/unsubscribe and PCM callback | mute, detach, reconnect |
| Android accepted-attempt queue | Android | one | exact attempt identity and decoded position | accept/reject/flush/fail |
| Platform route buffer | Android/audio HAL | device/route-owned and opaque | decoded callback plus route callbacks | mute and route-local reconciliation |

The opaque transport buffers cannot violate the product bound because media
cannot enter them without the one durable admitted attempt. When their contents
become uncertain, the route is muted and the same canonical segment is
reissued; later speech cannot cross the admission seam.

## Measurements and reproducibility

The original Phase C Pixel run used physical session
`ses_2648584333f9dd384f3e2c6eb3e37eb6`, logical session
`ses_0a1821d30918a640522c`, and operation
`a55525e8-fb65-4347-be57-9d7b71a6ab6e`. Android evidence stopped at
00:28:34.236Z, the worker continued publication until 00:28:42.655Z, and
LiveKit reported reconnect only at 00:28:49.446Z. The pre-protocol path
therefore admitted more than eight seconds after receiver evidence ceased and
resumed at the realtime media head, skipping semantic items.

The first `W=1` reconnect fix repeated the uncertain count item rather than
skipping later items. Completion-to-next-start gaps measured 1.3–1.6 seconds;
0.6–0.95 seconds was serialized synthesis. One local synthesized lookahead
reduced the observed handoff to roughly 0.4–0.6 seconds without increasing
admitted depth. The final deterministic-source run,
`ses_f6dffefb08271664a5f815a5fd4709fb`, confirmed bounded subdivision,
interruption, and resume at exactly the next logical sentence.

Run the content-free schedule suite with:

```sh
make check-playback-conformance
make check-playback-model
```

The schedule harness uses stable numeric generation, segment, and playback
identities and exercises immediate delivery, slow synthesis, delayed Android
consumption, media before metadata, media delay, disconnect/reconnect, worker
restart, receiver restart, background/foreground, Bluetooth-style route change,
and cancellation. It records no text or PCM. Its checked schedules observe a
maximum admitted depth of one and a maximum admitted duration of 2,000 ms
(against the 30,000 ms configured cap). TLC separately explores 1,194 distinct
states for three
segments, two generations, four bounded attempt identities, both process
restarts, cancellation, reconnect, and reissue.

Physical speaker, background/foreground, forced process restart, and Bluetooth
observations must be added to the ticket notes by the operator; the exact
acceptance sequence is in the final section.

## Ordering behavior

Reliable LiveKit data and realtime media are separate channels. There is no
cross-channel order guarantee. A reliable publish call returning does not prove
Android received the announcement, and a PCM callback does not identify which
reliable message arrived.

Revision 1 therefore uses a two-phase admission:

1. Android advertises `(receiver_epoch, credit_sequence, W, D)`.
2. The worker durably records one attempt and sends `PlaybackSegment`.
3. Android validates the attempt against current credit, reserves its only slot,
   durably snapshots `ACCEPTED`, and acknowledges it.
4. Only then may the worker publish media.
5. Android reports decoded-PCM `STARTED`, monotonic `POSITION`, and one terminal
   outcome.

Media attempted before step 3 is blocked at the sender. Stray media from a
superseded track has no current Android attempt and cannot mutate heard state.

## Protocol grammar

Every event uses Protocol v2. Playback protocol revision 1 adds:

- `receiver_epoch`: random per Android receiver process;
- `credit_sequence`: monotonic within the receiver epoch;
- `attempt_sequence`: monotonic within the worker process/ledger;
- `PLAYBACK_STATUS_ACCEPTED`: metadata and local capacity are reserved;
- `PlaybackReconciliationOutcome`: ready, replay pending, unsupported, stale
  credit, or invalid capacity.

An attempt identity is:

```text
(session_id, generation, segment_id, playback_id,
 receiver_epoch, credit_sequence, attempt_sequence)
```

`segment_id` is stable across delivery retries. `playback_id` and
`attempt_sequence` change on every retry. Reuse of an idempotency key with
different receiver credit is rejected. A duplicate known `playback_id` causes
Android to return its latest durable acknowledgement instead of enqueueing it.

## Authorities

| Fact | Sole authority |
| --- | --- |
| Canonical segment order and stable segment identity | worker |
| Generation, cancellation, and immutable delivery-attempt issuance | worker |
| Admitted attempt and retained PCM | durable worker ledger |
| Receiver epoch, credit sequence, capacity, route-local flush | Android |
| Metadata acceptance and decoded-PCM start/position/terminal | Android |
| Accepted heard/source boundary | worker, derived only from valid Android evidence |
| Data/media transport | LiveKit, with no product-state authority |

## Sender state machine

States are `not_ready`, `ready`, `announced`, `accepted`, `playing`,
`reconciling`, and `terminal`.

| Event | Transition |
| --- | --- |
| valid credit grant | `not_ready -> ready` |
| canonical item with credit | durable write, `ready -> announced` |
| matching `ACCEPTED` | `announced -> accepted`, then publish media |
| matching `STARTED/POSITION` | `accepted -> playing -> playing` |
| matching terminal | retire ledger row, return credit, `* -> terminal -> ready` |
| disconnect/track uncertainty | `announced/accepted/playing -> reconciling`; no credit |
| matching completed snapshot | accept once, return credit |
| partial/absent snapshot | supersede delivery, reissue same segment/new attempt |
| cancellation | mark cancelled, flush, prevent all old-generation reissue |
| worker restart | load the one durable nonterminal row, reconcile before new work |
| unknown, malformed, stale identity | reject without mutation |
| exact duplicate | return prior result without mutation |

## Receiver state machine

States are `muted`, `credit_granted`, `accepted`, `started`, `terminal`, and
`reconciling`.

| Event | Transition |
| --- | --- |
| room and playback track ready | issue fresh credit grant |
| valid current `PlaybackSegment` with empty slot | durably snapshot `ACCEPTED` |
| duplicate known attempt | replay latest durable acknowledgement |
| PCM before an accepted attempt | ignore; no acknowledgement |
| first audible decoded PCM | `accepted -> started` |
| later decoded PCM | monotonic position |
| duration consumed | persist/send completed and free local slot |
| pause, interrupt, stop, route loss | mute, flush current attempt, reconcile |
| transport loss | mute and retain latest evidence |
| process restart | load content-free evidence, create new receiver epoch, reconcile |
| stale epoch/credit/generation or second attempt | reject without mutation |

## Credit conservation and timeouts

Initially Android owns one credit. A valid announcement transfers it to the
single attempt. `ACCEPTED`, `STARTED`, `POSITION`, time passage, publication,
and disconnect do not create credit. A modeled terminal outcome retires the
attempt before the next admission. Thus:

```text
available_credit + nonterminal_attempts = 1
admitted_unheard_ms <= nonterminal_attempts * 30,000
```

No wall-clock timeout claims hearing. A watchdog may request reconciliation or
fail the session closed; it cannot advance a boundary or silently admit later
speech.

## Reconnect and restart

Android mutes before reconciliation and waits for room plus playback-track
readiness. It sends its fresh credit identity and bounded latest evidence.

- Completed/terminal evidence retires the matching durable row idempotently.
- Partial, accepted-only, or absent evidence supersedes the old delivery and
  reissues the same canonical segment with a new attempt.
- Worker restart reloads the single retained PCM row before accepting new work.
- Android restart reloads only content-free acknowledgement evidence and creates
  a new receiver epoch.
- Cancellation always wins over a recovered row or delayed acknowledgement.

Exactly-once acoustic perception is impossible if Android played audio but both
its terminal evidence and app-private durable snapshot are lost. The protocol
chooses at-least-once delivery of the one uncertain segment rather than silently
skipping it. It guarantees exactly-once accepted heard-boundary advancement.

## Compatibility

New workers require playback revision 1, a nonempty receiver epoch, positive
credit/attempt sequences, `W=1`, and `0 < D <= 30,000`. Until that handshake
completes they fail closed and publish no audio. New Android remains muted until
it receives a matching revision-1 reconciliation or replacement attempt.
Therefore:

- new worker + old Android: no admission;
- old worker + new Android: Android remains muted because no valid reconciliation
  or revision-1 attempt arrives;
- new worker + new Android: bounded revision-1 path.

There is no silent fallback to eager/unbounded publication.

## Formal refinement and proof

The TLA+ variables map as follows:

| TLA+ | Protocol/implementation |
| --- | --- |
| `epoch` | operation generation |
| `nextSegment` | worker canonical boundary |
| `attemptNumber` | playback attempt sequence |
| `senderState` | durable attempt status |
| `receiverState` | `DevicePlaybackTracker` lifecycle |
| `credit` | Android one-attempt grant |
| `heardThrough` | accepted playback/source boundary |
| `connected` | usable data/media route |

Base case: there is no attempt, credit is one, and heard-through is zero.

Inductive step: admission is the only transition that can increase outstanding
audio, requires the one credit, and checks duration. It consumes credit before
transport publication. Nonterminal receiver evidence cannot return credit.
Completion advances exactly the current next boundary and then returns credit.
Reconnect either accepts terminal evidence or replaces the same segment after
superseding its old attempt. Restart preserves the durable row/evidence.
Cancellation removes the old-generation attempt before a new generation can
admit. Stale and duplicate events do not mutate current state. Therefore every
transition preserves the finite bound, canonical order, one current generation,
cancellation dominance, and single accepted boundary advancement.

Conditional liveness assumes: both processes eventually remain running, the
data/media route eventually remains connected, reliable messages are eventually
delivered, the audio sink continues consuming decoded PCM, and fair enabled
sender/receiver actions eventually run. Under those assumptions, an admitted
attempt is eventually accepted, completed, explicitly flushed/failed, or
reconciled and reissued. Because only finitely many failures precede the stable
suffix, every canonical segment eventually reaches a terminal state. During an
unbounded partition the safety bound remains, but completion is not promised.

## Transport limitations the model must retain

- LiveKit reliable data and realtime media have no cross-channel ordering.
- A realtime audio track cannot recover skipped semantic order.
- WebRTC jitter and platform route buffers expose no authoritative queued
  duration to application code.
- LiveKit connection events lag receiver evidence and cannot grant credit.
- Pipecat publication completion means frame handoff, not physical playback.
- Pipecat flush and Android route mute are different facts and require explicit
  reconciliation.
- Audio callbacks report decoded PCM but not a transport segment ID; the
  acceptance barrier and one-attempt window provide that correlation.

## Physical acceptance

Use the review APK produced from this branch and the pinned `claude-sonnet-5`
foreground/background profiles.

1. Ordinary model speech: ask for a count to twenty; verify no burst and prompt
   barge-in.
2. Source reading: start a multi-paragraph source, interrupt mid-sentence, then
   resume; verify no acknowledged sentence is skipped.
3. Partition: disable Tailscale after an audible item, wait ten seconds, restore
   it; the same uncertain item may repeat once, but no later backlog may burst.
4. Background/foreground: background the app during speech, wait, return; verify
   bounded silence/reconciliation rather than catch-up playback.
5. Android restart: force-stop and reopen during speech; verify reconciliation
   and no skipped acknowledged item.
6. Worker restart: restart the media worker during speech; verify the retained
   item replays before any later item.
7. Bluetooth: switch speaker to Bluetooth and back during speech; verify mute,
   same-segment recovery, and prompt interruption.
8. Cancellation dominance: interrupt during each of announcement, playback, and
   reconnect; no old-generation audio may resume.

Record session/operation IDs and pass/fail observations in `rad-grf7`; no
conversation text or microphone audio is required.
