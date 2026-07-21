---
id: rad-16wv
status: closed
deps: []
links: [rad-n62u, rad-20nk, rad-t0va, rad-jypn]
created: 2026-07-20T19:47:21Z
type: bug
priority: 0
assignee: coopwilliams
parent: rad-3se2
tags: [android, voice, interruption, playback, p0]
---
# Treat queued device playback as active foreground speech ownership

Android can keep speaking old response audio after the backend has marked the model turn complete. The media worker publishes synthesized segments without waiting for device playback, clears the active turn, and reports foreground_turn_active=false even while current-generation playback remains pending on the phone. Speech during that interval is submitted as a normal turn instead of barge-in, so no flush is sent and the replacement answer queues behind stale audio. This is distinct from rad-n62u: that ticket restores future playback after a confirmed interruption; this ticket ensures audible queued playback can still be interrupted. The separately observed Silero high-energy/zero-window classifier branch is tracked by rad-jypn.

## Design

Model foreground speech as one ownership lifecycle spanning generation, synthesis, publisher queue, and acknowledged device playback. Do not relinquish interruptible ownership merely because the model stream ended. Classify user speech as a barge-in while any current-generation audio is queued or audibly unacknowledged, flush the exact playback generation, and reconcile completion/flush acknowledgements before accepting replacement playback. Add protocol and conformance coverage for delayed Android playback after worker completion.

## Acceptance Criteria

1. A long Android response remains foreground-active until every current-generation segment is completed or flushed by the device. 2. Saying 'Okay, stop. What color is the sky on a clear day?' after worker completion but during queued playback causes a confirmed interruption, an acknowledged flush of the old generation, and prompt audible playback of the sky answer. 3. No stale segment becomes audible after the flush boundary, and later turns remain audible. 4. Automated tests reproduce session ses_eb23d75121af2c72f8c55641adab1136's ordering. 5. The playback ledger correlates turn generation, operation, segment, flush request, and device acknowledgement.


## Notes

**2026-07-20T19:48:12Z**

Observed 2026-07-20 in ses_eb23d75121af2c72f8c55641adab1136: media turn completed at 17:13:10.442407Z; final old-response device segment started 17:13:12.802Z for 20.034s; interruption utterance persisted 17:13:25.220Z; replacement answer persisted 17:13:27.778Z but did not start playback until 17:13:32.836Z, 3ms after stale playback completed. No FLUSHED playback acknowledgement or interrupted turn disposition was recorded. In ses_5fb0ac5ab311fbda54bb9652cb9b68b0, three genuine overlap candidates were retained as unconfirmed_barge_in after Silero returned speech_windows=0 (max probabilities .1565/.2015/.3709) despite RMS up to 12517.

**2026-07-21T02:30:19Z**

Implemented the queued-playback ownership fix on 2026-07-20. The worker now remains foreground-active/SPEAKING after model persistence until all current-generation device segments are completed or flushed; verified interruption advances generation while accepting only terminal acknowledgements from the old generation. Unit regression reproduces post-model queued playback and rejects stale nonterminal acknowledgements. Local Qwen/LiveKit conformance report artifacts/voice-conformance/20260721T022752Z/report.json passed: 15 playback attempts remained queued after Qwen active_turns reached 0; speech interruption acknowledged in 0.388s; old audio stopped in 0.417s; replacement generation 5 followed interrupted generation 4; replacement was audible in 2.267s; all automatic FLUSHED acknowledgements persisted. Full Python: 318 passed, 1 skipped. Physical-phone confirmation remains pending; the separate zero-speech-window classifier branch is not claimed fixed by this change.

**2026-07-21T02:40:50Z**

Physical Qwen/Android confirmation completed in ses_30fb59e584c583edcd17f327a5d202bb. The count operation e4fa8d0b-4309-43d5-81c0-1b317d821272 persisted its partial exact sequence through 117 at 02:33:30.290Z, recorded Turn interrupted at 02:33:30.297Z, and the phone durably FLUSHED its active 5.540s segment plus one queued segment at 02:33:31.773Z. The replacement pineapple request persisted at 02:33:34.855Z, began device playback in generation 2 at 02:33:38.362Z, and remained interruptible: a later request flushed its remaining generation-2 queue, followed by multiple completed audible turns through 02:38:13.444Z. The user's phone observation confirms the old response stopped rather than resuming. Ownership acceptance is satisfied. The distinct high-energy/zero-Silero-window risk was split to P0 rad-jypn before closure; unrelated job/status and model-quality behavior is outside this ticket.
