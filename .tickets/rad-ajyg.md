---
id: rad-ajyg
status: open
deps: [rad-oy5k]
links: [rad-oy5k]
created: 2026-07-26T02:53:41Z
type: feature
priority: 2
assignee: coopwilliams
tags: [radcliffe, source, playback, seek, voice, semantic, post-m1]
---
# Semantic seek: move the playhead to a described location, not a literal phrase

As a listener, I want to seek by MEANING rather than by literal phrase: "skip ahead to where the interview begins in earnest", "jump to where she starts talking about her childhood", "get past the preamble". There is no exact string to search for - the target is a described place in the document, recognisable only by reading it.

This is the semantic counterpart to rad-oy5k. That ticket does literal Control-F matching, which cannot serve these requests: the words "the interview begins in earnest" appear nowhere in the text.

Shape: the agent calls a tool that pages through the source in chunks, evaluating each against the criterion the user described, until it identifies the boundary where the criterion is first met. It then moves the playhead to that sentence and reading resumes from there.

P2: strictly a convenience on top of literal seek, which is itself P1. The app is usable without either.

## Design

Depends on rad-oy5k for the playhead mechanism - once a target boundary is chosen, moving there and resuming must use exactly the same seek marker and baseline-reset logic, not a second parallel path.

ARCHITECTURAL NOTE, the significant one: this deliberately punches a controlled hole in the property that the model never sees unread source text. Today read_source never appends the source envelope to the store; only heard: envelopes reach context. Semantic seek requires the model to actually read ahead in order to judge. Consequences to accept explicitly:

- The exact-source guarantee is NOT weakened. That guarantee is about what is SPOKEN: playback still streams canonical bytes to TTS without passing through the model. Only the seek decision is model-judged.
- But more source text is sent to Anthropic than before (data_boundary is already cloud_anthropic). For a 92k-char article, paging the whole thing through context is a real cost and latency event. Needs a bound, not an unbounded scan.

DECISIONS TO SETTLE:

1. Hallucination guard - the important one. A model asked "where does the interview begin" will happily assert a location it did not actually find. Require the tool to return the verbatim sentence it is anchoring on, then VALIDATE that the quoted text genuinely occurs in the chunk that was examined; reject and continue if it does not. Without this, semantic seek silently jumps to plausible-sounding wrong places, which is the failure mode users trust least.

2. Chunk size and overlap. A criterion boundary can straddle a chunk edge ("...and then the interview began." at the last sentence of a chunk). Overlap chunks by some sentences, or evaluate on a sliding window. Decide the unit: sentences (aligns with boundary indices) rather than characters.

3. Bounded search with a defined exhaustion outcome. Cap chunks examined and/or tokens spent. When the cap is hit without a match, say so plainly - "I read through the first third and did not find where the interview starts" - rather than guessing. Decide whether the cap is chunk count, token budget, or fraction of document.

4. Direction and origin. Default forward from the current playhead. "Go back to where she was discussing X" needs backward paging. Whole-document search should probably be opt-in given the cost.

5. SETTLED - jump, do not confirm. A semantic guess is fallible in a way a literal match is not, but undo is just another seek, so the cost of being wrong is low and a confirmation prompt on every jump is friction on the common case where it is right. The tool still states where it landed and why ("about a third in, right after she finishes describing the venue") so a wrong jump is recognisable immediately and correctable by voice.

6. Interaction with rad-oy5k's no-match semantics. Keep the messages distinguishable: literal no-match, semantic not-found-within-budget, and not-found-in-document are three different things.

## Acceptance Criteria

A spoken request to seek to a described location - not a literal phrase - moves playback to a sentence the operator judges correct, for at least the motivating cases ("where the interview begins in earnest", "past the preamble"). The tool pages through the source in bounded chunks and reports which sentence it selected and why. Any claimed anchor text is validated to occur verbatim in the examined chunk before the playhead moves, so a hallucinated location cannot cause a jump. When the criterion is not found within budget, the user is told plainly that the search was bounded and unsuccessful, and playback state is unchanged. Moving the playhead reuses the rad-oy5k seek marker so a later plain resume continues correctly. Playback itself still streams canonical bytes to TTS without passing through the model, and the content hash and exact-source guarantee are unaffected. Cost of a semantic seek over a long article is measured and recorded in the ticket before it is considered acceptable. Verified by a Pixel listen-through on a long article with a clear structural boundary.
