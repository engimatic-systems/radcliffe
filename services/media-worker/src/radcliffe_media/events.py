from __future__ import annotations

from google.protobuf.timestamp_pb2 import Timestamp
from radcliffe_domain.envelope import MessageEnvelope, SourceKind
from radcliffe_domain.jobs import (
    JobEventKind,
    JobEventRecord,
    JobNotificationPolicy,
    JobStatus,
)
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

_SOURCE_TO_PROTO = {
    SourceKind.SYSTEM: protocol_pb2.SOURCE_KIND_SYSTEM,
    SourceKind.USER: protocol_pb2.SOURCE_KIND_USER,
    SourceKind.MODEL: protocol_pb2.SOURCE_KIND_MODEL,
    SourceKind.CALL: protocol_pb2.SOURCE_KIND_CALL,
    SourceKind.TEXT_SOURCE: protocol_pb2.SOURCE_KIND_TEXT_SOURCE,
}

_JOB_STATUS_TO_PROTO = {
    JobStatus.QUEUED: protocol_pb2.JOB_STATUS_QUEUED,
    JobStatus.RUNNING: protocol_pb2.JOB_STATUS_RUNNING,
    JobStatus.WAITING: protocol_pb2.JOB_STATUS_WAITING,
    JobStatus.SUCCEEDED: protocol_pb2.JOB_STATUS_SUCCEEDED,
    JobStatus.FAILED: protocol_pb2.JOB_STATUS_FAILED,
    JobStatus.CANCELLED: protocol_pb2.JOB_STATUS_CANCELLED,
}

_JOB_POLICY_TO_PROTO = {
    JobNotificationPolicy.IMMEDIATE: protocol_pb2.JOB_NOTIFICATION_POLICY_IMMEDIATE,
    JobNotificationPolicy.NEXT_SAFE_PAUSE: (protocol_pb2.JOB_NOTIFICATION_POLICY_NEXT_SAFE_PAUSE),
    JobNotificationPolicy.SILENT: protocol_pb2.JOB_NOTIFICATION_POLICY_SILENT,
    JobNotificationPolicy.USER_WAITING: protocol_pb2.JOB_NOTIFICATION_POLICY_USER_WAITING,
}

_JOB_EVENT_KIND_TO_PROTO = {
    JobEventKind.ACCEPTED: protocol_pb2.JOB_EVENT_KIND_ACCEPTED,
    JobEventKind.STARTED: protocol_pb2.JOB_EVENT_KIND_STARTED,
    JobEventKind.LEASE_RECOVERED: protocol_pb2.JOB_EVENT_KIND_LEASE_RECOVERED,
    JobEventKind.PROGRESS: protocol_pb2.JOB_EVENT_KIND_PROGRESS,
    JobEventKind.WAITING: protocol_pb2.JOB_EVENT_KIND_WAITING,
    JobEventKind.FOLLOW_UP_ADDED: protocol_pb2.JOB_EVENT_KIND_FOLLOW_UP_ADDED,
    JobEventKind.PRIORITY_CHANGED: protocol_pb2.JOB_EVENT_KIND_PRIORITY_CHANGED,
    JobEventKind.CANCELLATION_REQUESTED: (protocol_pb2.JOB_EVENT_KIND_CANCELLATION_REQUESTED),
    JobEventKind.CANCELLED: protocol_pb2.JOB_EVENT_KIND_CANCELLED,
    JobEventKind.SUCCEEDED: protocol_pb2.JOB_EVENT_KIND_SUCCEEDED,
    JobEventKind.FAILED: protocol_pb2.JOB_EVENT_KIND_FAILED,
}


class LiveKitEventSink:
    def __init__(self, transport: RadcliffeLiveKitTransport, session_id: str) -> None:
        self.transport = transport
        self.session_id = session_id

    async def message(self, envelope: MessageEnvelope) -> None:
        event = protocol_pb2.ServerEvent(protocol_version=2, message=envelope.to_proto())
        await self._send(event)

    async def session_ready(self, generation: int, participant_id: str) -> None:
        event = protocol_pb2.ServerEvent(
            protocol_version=2,
            session_ready=protocol_pb2.SessionReady(
                session_id=self.session_id,
                generation=generation,
            ),
        )
        await self._send(event, participant_id)

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
        operation_generation = operation.generation if operation is not None else generation
        reference = protocol_pb2.OperationRef(
            session_id=self.session_id,
            generation=operation_generation,
            operation_id=operation.operation_id if operation else "",
            cancellation_id=operation.cancellation_id if operation else "",
            trace_id=operation.trace_id if operation else "",
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

    async def playback_segment(
        self,
        operation: Operation,
        playback_id: str,
        segment_id: str,
        boundary_index: int,
        duration_ms: int,
        canonical_sha256: bytes,
        source: SourceKind,
        speech_rate_multiplier: float,
    ) -> None:
        event = protocol_pb2.ServerEvent(
            protocol_version=2,
            playback_segment=protocol_pb2.PlaybackSegment(
                operation=protocol_pb2.OperationRef(
                    session_id=operation.session_id,
                    generation=operation.generation,
                    operation_id=operation.operation_id,
                    cancellation_id=operation.cancellation_id,
                    trace_id=operation.trace_id,
                ),
                playback_id=playback_id,
                segment_id=segment_id,
                boundary_index=boundary_index,
                duration_ms=duration_ms,
                canonical_sha256=canonical_sha256,
                source=_SOURCE_TO_PROTO[source],
                speech_rate_multiplier=speech_rate_multiplier,
            ),
        )
        await self._send(event)

    async def agent_control_request(
        self,
        request: protocol_pb2.AgentControlActionRequest,
    ) -> None:
        event = protocol_pb2.ServerEvent(
            protocol_version=2,
            agent_control_request=request,
        )
        await self._send(event)

    async def job_event(
        self,
        record: JobEventRecord,
        participant_id: str | None = None,
    ) -> None:
        occurred_at = Timestamp()
        occurred_at.FromDatetime(record.occurred_at)
        event = protocol_pb2.ServerEvent(
            protocol_version=2,
            job_event=protocol_pb2.JobEvent(
                schema_version=1,
                event_id=record.event_id,
                job=protocol_pb2.JobRef(
                    job_id=record.job_id,
                    session_id=self.session_id,
                    revision=record.revision,
                    status=_JOB_STATUS_TO_PROTO[record.status],
                ),
                kind=_JOB_EVENT_KIND_TO_PROTO[record.kind],
                notification_policy=_JOB_POLICY_TO_PROTO[record.notification_policy],
                priority=record.priority,
                detail_code=record.detail_code,
                entity_ids=record.entity_ids,
                artifact_ids=record.artifact_ids,
                occurred_at=occurred_at,
                delivery_sequence=record.delivery_sequence,
            ),
        )
        await self._send(event, participant_id)

    async def _send(
        self,
        event: protocol_pb2.ServerEvent,
        participant_id: str | None = None,
    ) -> None:
        await self.transport.send_protocol(
            event.SerializeToString(deterministic=True),
            participant_id,
        )
