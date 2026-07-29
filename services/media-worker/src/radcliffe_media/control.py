from __future__ import annotations

from datetime import UTC
from typing import Protocol

from google.protobuf.message import DecodeError
from radcliffe_domain.session import (
    Operation,
    PlaybackAcknowledgement,
    PlaybackStatus,
    StaleOperationError,
    trace_id_for_operation,
    valid_trace_id,
)
from radcliffe_protocol.radcliffe.v2 import protocol_pb2

_PLAYBACK_STATUS = {
    protocol_pb2.PLAYBACK_STATUS_ACCEPTED: PlaybackStatus.ACCEPTED,
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

    async def reconnect(
        self,
        *,
        request_id: str,
        session_id: str,
        generation: int,
        latest_playback: tuple[PlaybackAcknowledgement, ...],
        max_inflight_attempts: int,
        max_segment_duration_ms: int,
        playback_protocol_revision: int,
        receiver_epoch: str,
        credit_sequence: int,
        idempotency_key: str,
    ) -> None: ...

    async def agent_control_confirmation(
        self,
        confirmation: protocol_pb2.AgentControlConfirmation,
        participant_identity: str,
    ) -> None: ...


class ClientControlHandler:
    def __init__(self, target: VoiceControlTarget) -> None:
        self.target = target

    async def handle(self, data: bytes, participant_identity: str | None = None) -> bool:
        event = protocol_pb2.ClientEvent()
        try:
            event.ParseFromString(data)
        except DecodeError:
            return False
        if event.protocol_version != 2:
            return False
        payload = event.WhichOneof("payload")
        if payload == "playback_acknowledgement":
            acknowledgement = _decode_playback_acknowledgement(event.playback_acknowledgement)
            if acknowledgement is None:
                return False
            try:
                await self.target.playback_acknowledgement(acknowledgement)
            except (StaleOperationError, ValueError):
                return False
            return True
        if payload == "reconnect":
            reconnect = event.reconnect
            latest: list[PlaybackAcknowledgement] = []
            for item in reconnect.latest_playback:
                acknowledgement = _decode_playback_acknowledgement(item)
                if acknowledgement is None:
                    return False
                latest.append(acknowledgement)
            if (
                not reconnect.request_id
                or not reconnect.session_id
                or reconnect.max_inflight_attempts != 1
                or reconnect.max_segment_duration_ms <= 0
                or reconnect.playback_protocol_revision != 1
                or not reconnect.receiver_epoch
                or reconnect.credit_sequence <= 0
            ):
                return False
            try:
                await self.target.reconnect(
                    request_id=reconnect.request_id,
                    session_id=reconnect.session_id,
                    generation=reconnect.generation,
                    latest_playback=tuple(latest),
                    max_inflight_attempts=reconnect.max_inflight_attempts,
                    max_segment_duration_ms=reconnect.max_segment_duration_ms,
                    playback_protocol_revision=reconnect.playback_protocol_revision,
                    receiver_epoch=reconnect.receiver_epoch,
                    credit_sequence=reconnect.credit_sequence,
                    idempotency_key=reconnect.idempotency_key,
                )
            except (StaleOperationError, ValueError):
                return False
            return True
        if payload == "agent_control_confirmation":
            if not participant_identity:
                return False
            confirmation = event.agent_control_confirmation
            if confirmation.origin.actor_id != participant_identity:
                return False
            await self.target.agent_control_confirmation(
                confirmation,
                participant_identity,
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


def _decode_playback_acknowledgement(
    acknowledgement: protocol_pb2.PlaybackAcknowledgement,
) -> PlaybackAcknowledgement | None:
    status = _PLAYBACK_STATUS.get(acknowledgement.status)
    if (
        status is None
        or not acknowledgement.HasField("occurred_at")
        or not acknowledgement.receiver_epoch
        or acknowledgement.credit_sequence <= 0
        or acknowledgement.attempt_sequence <= 0
    ):
        return None
    operation = acknowledgement.operation
    trace_id = operation.trace_id or trace_id_for_operation(operation.operation_id)
    if not valid_trace_id(trace_id):
        return None
    return PlaybackAcknowledgement(
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
        receiver_epoch=acknowledgement.receiver_epoch,
        credit_sequence=acknowledgement.credit_sequence,
        attempt_sequence=acknowledgement.attempt_sequence,
    )
