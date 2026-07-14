import pytest
from radcliffe_domain.envelope import MessageEnvelope, SourceKind
from radcliffe_domain.session import ConversationState
from radcliffe_media.events import LiveKitEventSink
from radcliffe_protocol.radcliffe.v2 import protocol_pb2


class FakeTransport:
    def __init__(self) -> None:
        self.packets: list[bytes] = []

    async def send_protocol(self, data: bytes, participant_id=None) -> None:
        self.packets.append(data)


@pytest.mark.asyncio
async def test_livekit_event_sink_sends_binary_state_and_message_events() -> None:
    transport = FakeTransport()
    sink = LiveKitEventSink(transport, "s1")  # type: ignore[arg-type]
    envelope = MessageEnvelope.text(
        session_id="s1", sequence=1, source=SourceKind.USER, text="hello"
    )

    await sink.state_changed(
        ConversationState.LISTENING,
        ConversationState.THINKING,
        1,
        "transcription complete",
    )
    await sink.message(envelope)

    state = protocol_pb2.ServerEvent.FromString(transport.packets[0])
    message = protocol_pb2.ServerEvent.FromString(transport.packets[1])
    assert state.state_changed.current == protocol_pb2.CONVERSATION_STATE_THINKING
    assert state.state_changed.reason == "transcription complete"
    assert message.message.content.inline_text == "hello"
