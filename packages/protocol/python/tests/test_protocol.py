from radcliffe_protocol.radcliffe.v2.protocol_pb2 import (
    SENSITIVITY_PRIVATE,
    SOURCE_KIND_USER,
    ClientEvent,
    ContentRef,
    MessageEnvelope,
)


def test_message_envelope_round_trip_preserves_identity_and_unknown_safe_shape() -> None:
    envelope = MessageEnvelope(
        schema_version=1,
        message_id="msg-1",
        session_id="session-1",
        sequence=1,
        source=SOURCE_KIND_USER,
        content=ContentRef(mime_type="text/plain", byte_length=5, inline_text="hello"),
        sensitivity=SENSITIVITY_PRIVATE,
    )
    wire = ClientEvent(protocol_version=2, message=envelope).SerializeToString(deterministic=True)

    parsed = ClientEvent.FromString(wire)

    assert parsed.protocol_version == 2
    assert parsed.WhichOneof("payload") == "message"
    assert parsed.message.message_id == "msg-1"
    assert parsed.message.content.inline_text == "hello"
