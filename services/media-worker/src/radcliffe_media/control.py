from __future__ import annotations

from datetime import UTC
from typing import Protocol

from google.protobuf.message import DecodeError
from radcliffe_domain.session import (
    Operation,
    PlaybackAcknowledgement,
    PlaybackStatus,
    trace_id_for_operation,
    valid_trace_id,
)
from radcliffe_protocol.radcliffe.v2 import protocol_pb2

_PLAYBACK_STATUS = {
    protocol_pb2.PLAYBACK_STATUS_STARTED: PlaybackStatus.STARTED,
    protocol_pb2.PLAYBACK_STATUS_POSITION: PlaybackStatus.POSITION,
    protocol_pb2.PLAYBACK_STATUS_COMPLETED: PlaybackStatus.COMPLETED,
    protocol_pb2.PLAYBACK_STATUS_FLUSHED: PlaybackStatus.FLUSHED,
    protocol_pb2.PLAYBACK_STATUS_FAILED: PlaybackStatus.FAILED,
}


class VoiceControlTarget(Protocol):
    async def interrupt(self) -> None: ...

    async def pause(self) -> None: ...

    async def resume(self) -> None: ...

    async def stop(self) -> None: ...

    async def playback_acknowledgement(self, acknowledgement: PlaybackAcknowledgement) -> None: ...


class ClientControlHandler:
    def __init__(self, target: VoiceControlTarget) -> None:
        self.target = target

    async def handle(self, data: bytes) -> bool:
        event = protocol_pb2.ClientEvent()
        try:
            event.ParseFromString(data)
        except DecodeError:
            return False
        if event.protocol_version != 2:
            return False
        payload = event.WhichOneof("payload")
        if payload == "playback_acknowledgement":
            acknowledgement = event.playback_acknowledgement
            status = _PLAYBACK_STATUS.get(acknowledgement.status)
            if status is None or not acknowledgement.HasField("occurred_at"):
                return False
            operation = acknowledgement.operation
            trace_id = operation.trace_id or trace_id_for_operation(operation.operation_id)
            if not valid_trace_id(trace_id):
                return False
            await self.target.playback_acknowledgement(
                PlaybackAcknowledgement(
                    acknowledgement_id=acknowledgement.acknowledgement_id,
                    operation=Operation(
                        session_id=operation.session_id,
                        generation=operation.generation,
                        operation_id=operation.operation_id,
                        cancellation_id=operation.cancellation_id,
                        trace_id=trace_id,
                    ),
                    segment_id=acknowledgement.segment_id,
                    status=status,
                    position_ms=acknowledgement.position_ms,
                    boundary_index=acknowledgement.boundary_index,
                    occurred_at=acknowledgement.occurred_at.ToDatetime(tzinfo=UTC),
                    detail=acknowledgement.detail,
                    playback_id=acknowledgement.playback_id,
                )
            )
            return True
        if payload != "control_command":
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
