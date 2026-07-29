# Bounded playback reconnect slice

Status: implemented as the first vertical slice of `rad-0tmq`. This document
describes temporary transport recovery while the Android and worker processes
remain alive. Durable process-restart recovery and the complete model checked
protocol remain work for the parent epic.

## Evidence and seam

In the Phase C Pixel trace, Android stopped producing playback evidence after
model boundary 6 while the worker admitted later boundaries for more than eight
seconds. LiveKit then resumed its current realtime media position, so the user
heard boundary 7 without hearing 5 and 6 in the spoken count. A realtime audio
track cannot restore that semantic order.

The enforceable sender seam is immediately before `PlaybackSegment` and its
corresponding audio frame enter LiveKit. The receiver signal is the terminal
decoded-PCM acknowledgement from `DevicePlaybackTracker`. Reconnect uses the
already-defined receiver snapshot rather than treating a LiveKit connection
event as evidence that any audio was heard.

## Ownership

| State or fact | Authority |
| --- | --- |
| Canonical segment order and stable `segment_id` | media worker |
| Immutable delivery-attempt `playback_id` | media worker |
| Whether decoded PCM started, progressed, or completed | Android |
| Receiver window and maximum admitted segment duration | Android |
| Current session generation and cancellation | media worker |
| Delivery of data and realtime media | LiveKit, with no product-state authority |
| Accepted heard/source boundary ledger | media worker, from valid Android evidence only |

## Slice state machine

The sender has `ready`, `waiting(attempt)`, and `reconciling(attempt?)` states.
The receiver has `connected`, `disconnected`, and `reconciling` states. An
attempt has a monotonic lifecycle:

`announced -> started -> position* -> completed`

or:

`announced -> flushed|failed|superseded`

`superseded` is sender-local. It retires an uncertain transport attempt without
claiming the user heard or flushed it. A retry retains the same `segment_id` and
boundary but receives a new `playback_id`.

The bounded slice fixes the receiver window at one attempt and negotiates a
maximum attempt duration. The worker synthesizes before admission, rejects audio
over the negotiated duration, announces one attempt, publishes its audio, and
does not admit the next canonical segment until Android reports a terminal
status. Wall-clock expiry does not return credit: the worker waits for receiver
evidence, reconnect reconciliation, or explicit cancellation.

On `RoomEvent.Reconnecting`, Android mutes playback, removes its uncertain local
attempt, and retains the latest acknowledgement for each recent attempt. After
connection it sends `Reconnect`. The worker flushes its realtime output before
reconciliation:

- A matching `completed` snapshot completes the retained segment without replay.
- A matching partial snapshot, or absence of the outstanding attempt from the
  bounded snapshot, supersedes that delivery attempt and retries the segment.
- `flushed` or `failed` remains terminal; cancellation and stale generation
  dominate replay.
- With no outstanding attempt, the worker responds with `replay_pending=false`.

Android stays muted until it accepts the replacement `PlaybackSegment`, or
until `PlaybackReconciled` says there is no replay pending.

## Safety argument

Let `W = 1` be the admitted nonterminal-attempt window and `D` the negotiated
maximum segment duration.

Initially there is no admitted attempt, so admitted unheard audio is zero.
Synthesis does not change admitted audio. The only admission transition requires
no other nonterminal attempt and `duration <= D`, so it establishes an upper
bound of `W * D = D`. Started and position acknowledgements cannot increase the
bound. A terminal acknowledgement retires the attempt before the next admission.
A disconnect does not create credit. Reconciliation either retires completed or
terminal work, or supersedes the one uncertain attempt before issuing exactly
one replacement; it never admits a later segment first. Cancellation retires
the old generation and prevents its replay. Therefore every transition
preserves both the finite admitted-unheard bound and canonical admission order.

During an arbitrary partition this slice guarantees bounded retention, not
exactly-once hearing. If Android heard an attempt but its completion evidence is
lost, reconnect may repeat that one segment. It will not silently skip that
segment and continue with later speech. Process failure can still lose the
in-memory retained audio and is explicitly outside this slice.

## Compatibility

A new Android client advertises one in-flight attempt and a finite duration.
The production worker also uses those conservative defaults before the first
snapshot. Older workers ignore the additive reconnect fields. The full epic
must add explicit capability negotiation and fail-closed restart behavior before
claiming the complete protocol.
