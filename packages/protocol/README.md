# Radcliffe Protocol v2

`proto/radcliffe/v2/protocol.proto` is the canonical cross-process contract.
Python and Kotlin/Java-lite bindings are checked in so Android and service
builds never depend on an ambient Protobuf compiler. Regenerate them with
`make generate-protocol`; generation deliberately rejects any compiler except
`libprotoc 33.6`, the newest line compatible with Pipecat's Protobuf 6 runtime.

All reliable LiveKit data packets carry a serialized `ClientEvent` or
`ServerEvent`. REST debug views may use Protobuf JSON mapping, but they must not
invent a second event shape.

## Agent-control actions

`AgentControlActionRequest` is the only Protocol v2 envelope through which an
agent-originated request may change speech delivery, allowlisted configuration,
or session lifecycle. Stable `AgentControlActionId` values must agree with the
typed `oneof action`; `ConfigurationAction` accepts only the enumerated keys and
matching value type. There is deliberately no generic setting name, arbitrary
JSON, path, URL, command, environment, credential, or process field.

The request carries its source/actor/message/tool provenance, exact target and
scope, operation generation, monotonic request sequence, idempotency key,
declared confirmation requirement, and request timestamp. Android receives a
pending request as a `ServerEvent.agent_control_request` and returns typed
confirmation or cancellation messages. The server reports the content-free
decision as `ServerEvent.agent_control_result`; authorization and application
are distinct dispositions.

Active Protocol v2 boundaries reject an unknown action ID, an unknown or absent
action payload, an unspecified enum, or a mismatched action ID/payload with a
stable `AgentControlRejectionCode`. Protobuf readers still preserve unknown
fields while forwarding an envelope so an additive minor-version field is not
lost. Adding a registered action, configuration key, enum value, or optional
field is minor-compatible; changing an existing ID, confirmation policy,
idempotency meaning, target meaning, or scope precedence requires a new field
or Protocol v3.

## Background jobs

`JobCommand` carries a bounded client lifecycle command, while `JobEvent` is the
privacy-minimized server projection. The event includes job/session IDs,
revision, status, notification policy, priority, a stable detail code, and
promoted entity/artifact IDs. A database-wide `delivery_sequence` gives every
session a stable ordered cursor even when multiple jobs interleave. It
deliberately has no job instruction,
follow-up text, raw output, worker transcript, or generic message-content
field. Event IDs and delivery sequences let clients replay and deduplicate
visibility after reconnect or restart.

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
- Agent-control retries with the same idempotency key and identical deterministic
  request bytes return the first durable result. Reuse with different bytes is
  `IDEMPOTENCY_CONFLICT`; two different requests occupying the same action,
  target, generation, scope, and request-sequence slot are `CONFLICT`.
- Reconnect reports the last envelope acknowledged by the client and its most
  recent playback acknowledgement. The server replays only later envelopes and
  resumes source audio from the acknowledged boundary.
- Each published audio segment has a unique playback-attempt ID. The client
  acknowledges decoded audio against that ID; retries or resumed segments get a
  new ID so stale acknowledgements cannot advance the durable cursor.

The compatibility test serializes fixtures with one binding and parses them
with the other. Generated files are reviewed only as compiler output; contract
review happens in the `.proto` file and this policy.
