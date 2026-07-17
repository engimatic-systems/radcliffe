import asyncio
from datetime import UTC, datetime

import pytest
from google.protobuf.timestamp_pb2 import Timestamp
from livekit import rtc
from radcliffe_domain.session import trace_id_for_operation
from radcliffe_media.control import ClientControlHandler
from radcliffe_media.livekit_adapter import RadcliffeLiveKitTransport
from radcliffe_protocol.radcliffe.v2 import protocol_pb2


class FakeControlTarget:
    def __init__(self) -> None:
        self.actions: list[str] = []
        self.acknowledgements = []

    async def interrupt(self) -> None:
        self.actions.append("interrupt")

    async def pause(self) -> None:
        self.actions.append("pause")

    async def resume(self) -> None:
        self.actions.append("resume")

    async def stop(self) -> None:
        self.actions.append("stop")

    async def playback_acknowledgement(self, acknowledgement) -> None:
        self.acknowledgements.append(acknowledgement)


def command(kind: int) -> bytes:
    return protocol_pb2.ClientEvent(
        protocol_version=2,
        control_command=protocol_pb2.ControlCommand(kind=kind),
    ).SerializeToString()


@pytest.mark.asyncio
async def test_protocol_v2_binary_controls_are_dispatched() -> None:
    target = FakeControlTarget()
    handler = ClientControlHandler(target)

    for kind in (
        protocol_pb2.ControlCommand.KIND_INTERRUPT,
        protocol_pb2.ControlCommand.KIND_PAUSE,
        protocol_pb2.ControlCommand.KIND_RESUME,
        protocol_pb2.ControlCommand.KIND_STOP,
    ):
        assert await handler.handle(command(kind))

    assert target.actions == ["interrupt", "pause", "resume", "stop"]
    assert not await handler.handle(b"not protobuf")


@pytest.mark.asyncio
async def test_protocol_v2_playback_acknowledgement_is_dispatched() -> None:
    target = FakeControlTarget()
    handler = ClientControlHandler(target)
    occurred_at = Timestamp()
    occurred_at.FromDatetime(datetime.now(UTC))
    event = protocol_pb2.ClientEvent(
        protocol_version=2,
        playback_acknowledgement=protocol_pb2.PlaybackAcknowledgement(
            acknowledgement_id="ack-1",
            playback_id="playback-1",
            operation=protocol_pb2.OperationRef(
                session_id="s1",
                generation=1,
                operation_id="operation-1",
                cancellation_id="cancel-1",
                trace_id="trc_" + "b" * 32,
            ),
            segment_id="segment-1",
            status=protocol_pb2.PLAYBACK_STATUS_COMPLETED,
            position_ms=1200,
            boundary_index=2,
            occurred_at=occurred_at,
        ),
    )

    assert await handler.handle(event.SerializeToString(deterministic=True))
    acknowledgement = target.acknowledgements[0]
    assert acknowledgement.playback_id == "playback-1"
    assert acknowledgement.segment_id == "segment-1"
    assert acknowledgement.position_ms == 1200
    assert acknowledgement.boundary_index == 2
    assert acknowledgement.operation.trace_id == "trc_" + "b" * 32

    legacy = protocol_pb2.ClientEvent.FromString(event.SerializeToString(deterministic=True))
    legacy.playback_acknowledgement.acknowledgement_id = "ack-legacy"
    legacy.playback_acknowledgement.operation.trace_id = ""
    assert await handler.handle(legacy.SerializeToString(deterministic=True))
    assert target.acknowledgements[1].operation.trace_id == trace_id_for_operation("operation-1")


@pytest.mark.asyncio
async def test_protocol_v2_rejects_malformed_trace_identity() -> None:
    target = FakeControlTarget()
    handler = ClientControlHandler(target)
    occurred_at = Timestamp()
    occurred_at.FromDatetime(datetime.now(UTC))
    event = protocol_pb2.ClientEvent(
        protocol_version=2,
        playback_acknowledgement=protocol_pb2.PlaybackAcknowledgement(
            acknowledgement_id="ack-invalid",
            playback_id="playback-invalid",
            operation=protocol_pb2.OperationRef(
                session_id="s1",
                generation=1,
                operation_id="operation-invalid",
                cancellation_id="cancel-invalid",
                trace_id="transcript content does not belong here",
            ),
            segment_id="segment-invalid",
            status=protocol_pb2.PLAYBACK_STATUS_COMPLETED,
            position_ms=1,
            boundary_index=1,
            occurred_at=occurred_at,
        ),
    )

    assert not await handler.handle(event.SerializeToString(deterministic=True))
    assert not target.acknowledgements


@pytest.mark.asyncio
async def test_livekit_transport_preserves_non_utf8_protocol_bytes() -> None:
    transport = RadcliffeLiveKitTransport("ws://127.0.0.1", "token", "room")
    received: list[bytes] = []

    @transport.event_handler("on_data_received")
    async def on_data(_transport, data: bytes, _participant_id: str) -> None:
        received.append(data)

    await transport._on_data_received(b"\x84\x00", "participant")
    await transport._client._async_on_data_received(
        rtc.DataPacket(data=b"\x85\x00", kind=0, participant=None)
    )
    await transport.send_protocol(b"not-connected-yet")
    await asyncio.sleep(0)

    assert received == [b"\x84\x00", b"\x85\x00"]
