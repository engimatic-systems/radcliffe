# Spoken-output policy

Radcliffe keeps generated display text and spoken delivery distinct when ordinary model output
contains machine-oriented detail. The durable model envelope and Android timeline retain the
original text. TTS receives a bounded, truthful alternative that names the kind of omitted detail
and tells the listener where it is available. This policy applies only to conversational model
output; canonical source rendering and explicit exact-output requests bypass it.

## Failure categories and alternatives

| Category | Representative display detail | Default spoken alternative |
| --- | --- | --- |
| Hashes | SHA-256 and long hexadecimal digests | “a hash shown in the conversation” |
| Identifiers | UUIDs and job, message, session, trace, request, operation, playback, or turn IDs | “an internal identifier shown in the conversation” |
| URLs | Full schemes, paths, and query strings | “a link on _spoken host name_” |
| Paths | POSIX, Windows, and multi-component relative paths | “a file path shown in the conversation” |
| Timestamps | ISO-8601 machine timestamps | A concise UTC date and time |
| Stack traces | Runtime frames with files and line numbers | “Technical details are available in the conversation” |
| Serialized data | Bounded JSON-like objects and arrays | “structured data available in the conversation” |
| Long numbers | Ten or more consecutive digits | “a long number shown in the conversation” |
| Code | Fenced blocks and inline code | “a code sample” or “inline code shown in the conversation” |
| Citation machinery | Bracketed numeric references | “a citation” |
| Repeated punctuation | Long punctuation runs | One instance of the punctuation |
| Acronym-heavy metadata | Four or more consecutive uppercase metadata tokens | “technical metadata shown in the conversation” |

The detector operates on complete sentence or line boundaries so provider chunks cannot expose a
partial URL, path, hash, or identifier before classification. Fenced code is held until model-message
completion. Category names—not matched content—are emitted as content-free telemetry and stored as
model-envelope metadata.

## Fidelity exceptions

- Requests using explicit exact, verbatim, spell-out, or character-by-character language preserve
  the requested bytes. The exact-output validator remains responsible for completeness.
- `read_source` and `read_job_source` continue through canonical source envelopes and never pass
  through conversational model-output substitution.
- The policy does not silently omit detail. Each alternative names what is available visually so a
  listener can decide whether to request exact delivery.

## Accessibility

Visual-only substitution is not sufficient for a blind or voice-only user. The spoken alternative
therefore always provides a categorical cue instead of silence, and an explicit request to read or
spell the value exactly bypasses substitution. The timeline retains the original value for TalkBack
and export, but automatically reading it through a screen reader would recreate the original hostile
experience. Follow-up `rad-c2tv` adds an Android/TalkBack affordance that announces availability and
offers an intentional exact-reading action.

Completed turns retain the full original display response. Each streaming display/spoken pair is
also mapped onto one or more model playback segments. The final playback segment in a pair owns its
display text, so an interruption or failure persists that raw display text only after every spoken
chunk in the pair reaches a durable completed boundary. This remains correct when the spoken
alternative is longer or shorter than its display counterpart. A queued but unacknowledged Android
segment owns no durable display text; later stale flush acknowledgements cannot add it after the
turn is closed. Partial-envelope length metadata and telemetry contain counts and category names,
never matched content.

## Evaluation

The reusable `scripts/spoken-output-conformance.py` corpus covers every category above plus exact
machine-string exceptions. Its reports retain only provider/model identity, token and cost totals,
per-category recurrence counts before and after spoken substitution, and pass/fail codes. Prompt and
response text are not written to reports.
