from __future__ import annotations

from typing import Protocol

from google.protobuf.message import DecodeError
from radcliffe_protocol.radcliffe.v2 import protocol_pb2


class VoiceControlTarget(Protocol):
    async def interrupt(self) -> None: ...

    async def pause(self) -> None: ...

    async def resume(self) -> None: ...

    async def stop(self) -> None: ...


class ClientControlHandler:
    def __init__(self, target: VoiceControlTarget) -> None:
        self.target = target

    async def handle(self, data: bytes) -> bool:
        event = protocol_pb2.ClientEvent()
        try:
            event.ParseFromString(data)
        except DecodeError:
            return False
        if event.protocol_version != 2 or event.WhichOneof("payload") != "control_command":
            return False
        kind = event.control_command.kind
        if kind == protocol_pb2.ControlCommand.KIND_INTERRUPT:
            await self.target.interrupt()
        elif kind == protocol_pb2.ControlCommand.KIND_PAUSE:
            await self.target.pause()
        elif kind == protocol_pb2.ControlCommand.KIND_RESUME:
            await self.target.resume()
        elif kind == protocol_pb2.ControlCommand.KIND_STOP:
            await self.target.stop()
        else:
            return False
        return True
