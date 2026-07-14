import asyncio

import pytest
from livekit import rtc
from radcliffe_media.control import ClientControlHandler
from radcliffe_media.livekit_adapter import RadcliffeLiveKitTransport
from radcliffe_protocol.radcliffe.v2 import protocol_pb2


class FakeControlTarget:
    def __init__(self) -> None:
        self.actions: list[str] = []

    async def interrupt(self) -> None:
        self.actions.append("interrupt")

    async def pause(self) -> None:
        self.actions.append("pause")

    async def resume(self) -> None:
        self.actions.append("resume")

    async def stop(self) -> None:
        self.actions.append("stop")


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
