---
id: rad-57f3
status: open
deps: []
links: [rad-fksk]
created: 2026-07-25T21:41:14Z
type: feature
priority: 1
assignee: coopwilliams
parent: rad-2tft
tags: [radcliffe, source, extraction, speech, listenability]
---
# Make extracted sources listenable instead of markup-shaped

Spoken source playback reads markdown link syntax, URLs, and navigation scaffolding aloud. Gwern.net is the worst case; most other sites extract cleanly.

## Design

Measured on the real artifacts under var/jobs (structural counts, no article text):

nicotine.md (92,250 chars, 365 links): 15.3% of the entire file is characters inside link TARGETS (14,144 chars of URLs and paths that should never reach TTS). 152 site-relative, 117 absolute, 96 in-page anchors. 134 links are glued to the preceding word with no space, which is why 'it affects[tons of systems](...)' is heard as running together. 154 instances of space-before-punctuation (' .', ' ,') produce awkward pauses. 39 lines are nothing but a link (table-of-contents and navigation shaped).

gwern_about.md (76,836 chars, 423 links): every single target is the literal string 'url' - 432 occurrences. That is Anthropic's native WebFetch markdown conversion, not Trafilatura.

Headline finding: this is not one mis-set extraction flag. The job agent has at least four independent ways to produce a source artifact, each with a different noise profile - native WebFetch (url placeholders), download_page/Trafilatura via copy_text_to_markdown (real URLs), run_command with an arbitrary CLI invocation, and the model simply writing prose (which is the separate fidelity bug where a summary was produced instead of an extraction). page_preview.extracted_page already passes favor_precision=True and leaves include_links at its default of False, so tuning it alone would not have touched the WebFetch-produced file at all.

Two distinct places to fix, with different tradeoffs:

(a) Extraction time - remove navigation, table-of-contents, and metadata scaffolding that should not be in the document at all. Changes the stored artifact and its content hash, so displayed and spoken text stay in agreement.

(b) Render time - normalize markdown to speech in the render projection, leaving canonical_bytes and the content hash untouched. This mirrors how SpeechDelivery already works: a property of the render plan, not a mutation of stored content. It preserves the exact-source guarantee that the whole product rests on.

The user's constraint is that brackets and parentheses cannot simply be deleted, because sometimes they are real prose. The answer is to parse rather than strip: resolve [label](target) to its label and drop the target; leave a bare parenthetical untouched because it is not a link. Same for emphasis markers. Character-level stripping is explicitly the wrong approach.

## Acceptance Criteria

Spoken source text contains no URLs, no markdown link or emphasis syntax, and no navigation or table-of-contents scaffolding; genuine parenthetical and bracketed prose is preserved verbatim; the canonical source hash and the exact-source guarantee are unaffected by any render-time normalization; word spacing around unwrapped links and punctuation is correct; normalization is deterministic and unit-tested against the gwern fixtures; and a Pixel listen-through of a gwern article is judged listenable by the operator.

