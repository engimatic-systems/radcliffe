from __future__ import annotations

from google.protobuf.timestamp_pb2 import Timestamp
from radcliffe_domain.envelope import MessageEnvelope
from radcliffe_domain.session import ConversationState, Operation
from radcliffe_protocol.radcliffe.v2 import protocol_pb2

from radcliffe_media.livekit_adapter import RadcliffeLiveKitTransport

_STATE_TO_PROTO = {
    ConversationState.IDLE: protocol_pb2.CONVERSATION_STATE_IDLE,
    ConversationState.CONNECTING: protocol_pb2.CONVERSATION_STATE_CONNECTING,
    ConversationState.LISTENING: protocol_pb2.CONVERSATION_STATE_LISTENING,
    ConversationState.THINKING: protocol_pb2.CONVERSATION_STATE_THINKING,
    ConversationState.SPEAKING: protocol_pb2.CONVERSATION_STATE_SPEAKING,
    ConversationState.READING: protocol_pb2.CONVERSATION_STATE_READING,
    ConversationState.PAUSED: protocol_pb2.CONVERSATION_STATE_PAUSED,
    ConversationState.RECONNECTING: protocol_pb2.CONVERSATION_STATE_RECONNECTING,
    ConversationState.FAILED: protocol_pb2.CONVERSATION_STATE_FAILED,
}


class LiveKitEventSink:
    def __init__(self, transport: RadcliffeLiveKitTransport, session_id: str) -> None:
        self.transport = transport
        self.session_id = session_id

    async def message(self, envelope: MessageEnvelope) -> None:
        event = protocol_pb2.ServerEvent(protocol_version=2, message=envelope.to_proto())
        await self._send(event)

    async def state_changed(
        self,
        previous: ConversationState,
        current: ConversationState,
        generation: int,
        reason: str,
        operation: Operation | None = None,
    ) -> None:
        occurred_at = Timestamp()
        occurred_at.GetCurrentTime()
        reference = protocol_pb2.OperationRef(
            session_id=self.session_id,
            generation=generation,
            operation_id=operation.operation_id if operation else "",
            cancellation_id=operation.cancellation_id if operation else "",
        )
        event = protocol_pb2.ServerEvent(
            protocol_version=2,
            state_changed=protocol_pb2.StateChanged(
                operation=reference,
                previous=_STATE_TO_PROTO[previous],
                current=_STATE_TO_PROTO[current],
                reason=reason,
                occurred_at=occurred_at,
            ),
        )
        await self._send(event)

    async def _send(self, event: protocol_pb2.ServerEvent) -> None:
        await self.transport.send_protocol(event.SerializeToString(deterministic=True))
