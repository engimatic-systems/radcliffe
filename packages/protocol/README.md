# Radcliffe Protocol v2

`proto/radcliffe/v2/protocol.proto` is the canonical cross-process contract.
Python and Kotlin/Java-lite bindings are checked in so Android and service
builds never depend on an ambient Protobuf compiler. Regenerate them with
`make generate-protocol`; generation deliberately rejects any compiler except
`libprotoc 33.6`, the newest line compatible with Pipecat's Protobuf 6 runtime.

All reliable LiveKit data packets carry a serialized `ClientEvent` or
`ServerEvent`. REST debug views may use Protobuf JSON mapping, but they must not
invent a second event shape.

## Compatibility policy

- `protocol_version` is `2`. An endpoint rejects an unsupported major version
  before interpreting its payload.
- `schema_version` begins at `1` for `MessageEnvelope`. Readers accept known
  fields and preserve unknown Protobuf fields when forwarding a message.
- Existing field numbers, enum numeric values, and oneof meanings are never
  reused. Removed fields are reserved in the same change that removes them.
- Additive optional fields and new enum values are minor-compatible. Readers
  must handle an unknown enum as unspecified and use an explicit safe default.
- Semantic changes require a new field or message. A breaking wire or
  idempotency change requires Protocol v3.
- Stable request, command, call, message, acknowledgement, and idempotency IDs
  make retries safe. `(session_id, sequence)` is unique and monotonically
  increasing; duplicate IDs must return the first durable result.
- Reconnect reports the last envelope acknowledged by the client and its most
  recent playback acknowledgement. The server replays only later envelopes and
  resumes source audio from the acknowledged boundary.
- Each published audio segment has a unique playback-attempt ID. The client
  acknowledges decoded audio against that ID; retries or resumed segments get a
  new ID so stale acknowledgements cannot advance the durable cursor.

The compatibility test serializes fixtures with one binding and parses them
with the other. Generated files are reviewed only as compiler output; contract
review happens in the `.proto` file and this policy.
