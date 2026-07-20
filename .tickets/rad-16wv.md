---
id: rad-16wv
status: open
deps: []
links: [rad-n62u, rad-20nk, rad-t0va]
created: 2026-07-20T19:47:21Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-3se2
tags: [android, voice, interruption, playback, p0]
---
# Treat queued device playback as active foreground speech ownership

Android can keep speaking old response audio after the backend has marked the model turn complete. The media worker publishes synthesized segments without waiting for device playback, clears the active turn, and reports foreground_turn_active=false even while current-generation playback remains pending on the phone. Speech during that interval is submitted as a normal turn instead of barge-in, so no flush is sent and the replacement answer queues behind stale audio. A second observed branch rejects genuine overlapping phone speech as unconfirmed_barge_in when the Silero gate reports zero speech windows despite strong near-field energy. This is distinct from rad-n62u: that ticket restores future playback after a confirmed interruption; this ticket ensures audible queued playback can still be interrupted.

## Design

Model foreground speech as one ownership lifecycle spanning generation, synthesis, publisher queue, and acknowledged device playback. Do not relinquish interruptible ownership merely because the model stream ended. Classify user speech as a barge-in while any current-generation audio is queued or audibly unacknowledged, flush the exact playback generation, and reconcile completion/flush acknowledgements before accepting replacement playback. Preserve echo/noise rejection, but make the classifier's uncertain branch observable and robust to real near-field speech during speaker output. Add protocol and conformance coverage for delayed Android playback after worker completion and for the observed zero-speech-window classifier branch.

## Acceptance Criteria

1. A long Android response remains foreground-active until every current-generation segment is completed or flushed by the device. 2. Saying 'Okay, stop. What color is the sky on a clear day?' after worker completion but during queued playback causes a confirmed interruption, an acknowledged flush of the old generation, and prompt audible playback of the sky answer. 3. No stale segment becomes audible after the flush boundary, and later turns remain audible. 4. Real near-field speech matching the observed high-energy/zero-window case does not silently degrade to a normal queued turn; uncertain disposition is retained and surfaced. 5. Echo, room tone, and silence do not cause false interruptions. 6. Automated tests reproduce session ses_eb23d75121af2c72f8c55641adab1136's ordering and the classifier-rejection branch from ses_5fb0ac5ab311fbda54bb9652cb9b68b0. 7. Telemetry correlates turn, generation, operation, segment, candidate, disposition, flush request, and device acknowledgement.


## Notes

**2026-07-20T19:48:12Z**

Observed 2026-07-20 in ses_eb23d75121af2c72f8c55641adab1136: media turn completed at 17:13:10.442407Z; final old-response device segment started 17:13:12.802Z for 20.034s; interruption utterance persisted 17:13:25.220Z; replacement answer persisted 17:13:27.778Z but did not start playback until 17:13:32.836Z, 3ms after stale playback completed. No FLUSHED playback acknowledgement or interrupted turn disposition was recorded. In ses_5fb0ac5ab311fbda54bb9652cb9b68b0, three genuine overlap candidates were retained as unconfirmed_barge_in after Silero returned speech_windows=0 (max probabilities .1565/.2015/.3709) despite RMS up to 12517.
