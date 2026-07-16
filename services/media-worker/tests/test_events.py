import pytest
from radcliffe_domain.envelope import MessageEnvelope, SourceKind
from radcliffe_domain.session import ConversationState, Operation
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
    await sink.playback_segment(
        Operation("s1", 1, "operation-1", "cancel-1"),
        "playback-1",
        "segment-1",
        2,
        1250,
        b"a" * 32,
        SourceKind.TEXT_SOURCE,
    )

    state = protocol_pb2.ServerEvent.FromString(transport.packets[0])
    message = protocol_pb2.ServerEvent.FromString(transport.packets[1])
    playback = protocol_pb2.ServerEvent.FromString(transport.packets[2])
    assert state.state_changed.current == protocol_pb2.CONVERSATION_STATE_THINKING
    assert state.state_changed.reason == "transcription complete"
    assert message.message.content.inline_text == "hello"
    assert playback.playback_segment.playback_id == "playback-1"
    assert playback.playback_segment.segment_id == "segment-1"
    assert playback.playback_segment.duration_ms == 1250
    assert playback.playback_segment.source == protocol_pb2.SOURCE_KIND_TEXT_SOURCE
