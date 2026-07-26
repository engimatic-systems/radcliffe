---
id: rad-oy5k
status: open
deps: []
links: [rad-57f3, rad-ajyg, rad-86le]
created: 2026-07-26T02:49:45Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, source, playback, seek, voice]
---
# Voice-driven find-in-source: seek to next or previous phrase match

As a listener, I want to seek through a source by voice the way Control-F works: jump forward to the next occurrence of a spoken phrase, back to the previous one, and be told plainly when there is no match. Asking to "skip ahead to the part where she talks about Athena" currently fails silently.

This capability does not exist today, and it is not a prompt-wording gap. Two independent reasons:

1. read_source accepts only path, rate, and start_at_boundary (an integer sentence index), with extra="forbid" set on ReadSourceArguments. There is nowhere to put a phrase, and an invented argument is rejected outright.

2. The model cannot see unread text, by design. read_source builds the full source_envelope but never appends it to the store; only the CALL result ({source_message_id, content_sha256}, no text) is persisted, and thereafter only heard: envelopes built from acknowledged playback reach model context. So the model knows what has been read aloud and nothing beyond it, and cannot map a phrase to a sentence index it has never seen.

Sharpening the tool description would make this worse, not better: the model would guess a boundary number for unseen content and jump confidently to the wrong place. The search has to happen server-side, where the text actually is.

P1 rather than P0: the app is usable without it. Reading, interruption, and resume all work.

## Design

Put the search where the text is. Add a phrase argument alongside start_at_boundary; the control plane searches the source, resolves a hit to a sentence index, and hands that integer to the existing boundary machinery. The document still never enters model context, playback/heard-ledger/resume all work unchanged, and no new file access is involved - the search runs inside the capability grant that already authorized the read.

Sketch: startAtMatch: string plus a direction (forward default, backward), or two arguments. Direction naming should read naturally when the model fills it in from "go back to the previous mention".

DECIDED (1 and 2 settled with the operator; 3-7 confirmed as written):

1. SETTLED - search normalized text in BOTH directions. Match against speakable_text(sentence) rather than canonical bytes, so a match always corresponds to words the listener can actually hear. Note the point that made this seem asymmetric: forward search does NOT need to fall back to raw markdown. speakable_text(text: str) -> str is a pure function of a string with no dependence on playback state, so the server can normalize any sentence on demand, unread ones included. Forward and backward search are therefore symmetric. This also means a phrase occurring only inside a URL or link target correctly fails to match ahead of the playhead, not merely behind it. Makes rad-57f3's normalizer a functional dependency of search, not just a listenability fix.

2. SETTLED - move the playhead; keep max, reset its baseline. resume_boundary() is max(boundaries, default=0) over heard envelopes matching the content hash (projection.py:354). The max is deliberate: it stops a partial or out-of-order playback acknowledgement from regressing recorded progress, so it must not simply become "most recent". Instead a seek writes a marker envelope, and resume_boundary computes the max over heard envelopes recorded AFTER the most recent seek for that content hash. Progress within a playback run still cannot go backward; a seek begins a fresh run. Without this, read to 50 / seek back to 10 / hear 10-12 / interrupt / "resume" yields 51 instead of 13, and backward seek is broken the moment the user interrupts after it.

3. Skipped text must never be recorded as heard. Seeking from 9 to 214 must not mark 10-213 heard. _persist_heard_source builds from acknowledged_text so this should hold, but it needs an explicit test - a false heard range silently corrupts every later resume.

4. Match semantics: case-insensitive, whitespace-collapsed, word-boundary (so "Athena" does not hit "Athenaeum"). Voice transcription will not reproduce casing or punctuation. Explicitly NOT doing fuzzy/phonetic matching in this ticket: a confident jump to the wrong place is worse than a clean no-match. Revisit only if exact matching proves too brittle against ASR output for proper nouns.

5. Relative to current position, no wraparound. Forward = first match strictly after current boundary; backward = last match strictly before. Saying "next" repeatedly walks matches naturally. Do not wrap at the end; instead distinguish two outcomes so the model can say something useful:
   - phrase occurs nowhere in the document -> "no match"
   - phrase occurs only behind/ahead of the current position -> "no further matches, but it appears earlier/later"

6. No-match must leave playback state untouched. Do not stop, do not reset to 0, do not advance. The tool returns a no-match result and the model reports it; whatever was happening keeps happening.

7. On success, return the resolved boundary index and the matched sentence so the model can confirm what it found rather than silently jumping. Reading position is already surfaced in context labels via readingPosition().

## Acceptance Criteria

A spoken request to seek to a phrase moves playback to the next sentence whose SPOKEN text contains that phrase; a request for the previous mention moves backward; repeated "next" requests walk successive matches. Matching is case-insensitive and word-boundary and runs against normalized speakable text, so every match corresponds to words the listener can actually hear. When the phrase appears nowhere, the tool returns an explicit no-match the model relays to the user, and playback state is completely unchanged. "No matches at all" and "no further matches in this direction" are distinguishable. Text skipped over by a seek is never recorded in the heard ledger. After seeking backward and then interrupting, a plain resume continues from the actual last playback position rather than the furthest point ever heard - covered by a regression test, since this is the failure mode the current max-based resume_boundary would produce. The full source text still never enters model context, and the exact-source guarantee and content hash are unaffected. Verified by a Pixel listen-through: seek forward to a phrase, seek back to a previous mention, request a phrase that does not occur, and hear the correct response in each case.

