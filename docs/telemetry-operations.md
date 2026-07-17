# Content-free telemetry and diagnostics

Radcliffe writes local correlation events to `var/telemetry/events.jsonl` by
default. The directory is mode `0700` and the append-only file is mode `0600`.
The media worker, Pi sidecar, and control plane share one deliberately narrow
schema; Android playback is represented only when its Protocol v2
acknowledgement reaches the worker.

Every event has exactly these fields:

- schema and UTC occurrence time;
- bounded component, event, phase, and outcome names;
- a server-generated `trc_` trace ID and operation ID;
- a one-way `ses_` session pseudonym and generation;
- an optional nonnegative duration in milliseconds.

The schema has no field for a prompt, transcript, model/tool/source content,
filename, path, URL, device identity, error string, credential, or arbitrary
metadata. Session pseudonyms are SHA-256-derived with domain separation, so raw
session IDs do not enter telemetry. Names, outcomes, identifier lengths, and
durations are validated before append. A telemetry write failure cannot take
down an active voice turn.

## Correlation path

The media worker creates a distinct trace with each operation. `OperationRef`
carries it over Protocol v2 to Android playback announcements and back through
playback acknowledgements. The worker includes the same trace and operation in
its local Pi turn request; the sidecar propagates both through tool-gateway HTTP
requests. Malformed trace IDs are rejected at the sidecar, HTTP request model,
and Protocol acknowledgement decoder. Older Android builds may omit the newly
known field; the worker derives a bounded compatibility trace from the
server-generated operation ID instead of accepting client text.

Events cover turn/active-work start and terminal outcomes, first model/audio
and synthesis/publish phases, interruption, reconnect, Android playback,
provider failures, tool authorization/execution failures, and control-plane
readiness. The operations snapshot supplies disk/resource-pressure state.

## Export a diagnostic bundle

First refresh readiness, then export:

```sh
make export-diagnostics-local
```

The private `artifacts/diagnostics/bundle.json` contains:

- content-free readiness revision/status/reason codes;
- aggregate event counts and duration count/sum/maximum;
- fixed coverage counters for readiness, first audio, interruption, reconnect,
  provider/tool errors, active work, and resource pressure; and
- trace summaries that list only bounded component/phase/outcome data.

Metric keys use only the bounded component/event/phase/outcome vocabulary.
Trace IDs, operation IDs, and session pseudonyms never become metric labels, so
cardinality is independent of conversation count. The exporter accepts only
the exact event schema, drops malformed or extra-field lines with a visible
counter, strips endpoint/resource details from operations status, scans the
complete serialized bundle for configured secrets, and writes mode `0600`.

The bundle is safe for operational review, but it is still private metadata:
keep it in ignored `artifacts`, do not move it to agent-share by default, and do
not infer that it authorizes collection of message or audio content. Bounded
file rotation and deletion are handled by the subsequent retention ticket.
