from datetime import UTC, datetime

import pytest
from radcliffe_domain.envelope import MessageEnvelope, SourceKind
from radcliffe_domain.jobs import (
    JobEventKind,
    JobEventRecord,
    JobNotificationPolicy,
    JobStatus,
)
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
    await sink.session_ready(2, "participant-1")
    await sink.message(envelope)
    await sink.playback_segment(
        Operation("s1", 1, "operation-1", "cancel-1", "trc_" + "a" * 32),
        "playback-1",
        "segment-1",
        2,
        1250,
        b"a" * 32,
        SourceKind.TEXT_SOURCE,
        1.25,
    )
    await sink.job_event(
        JobEventRecord(
            event_id="job-event:job-1:3",
            delivery_sequence=9,
            job_id="job-1",
            revision=3,
            kind=JobEventKind.SUCCEEDED,
            status=JobStatus.SUCCEEDED,
            notification_policy=JobNotificationPolicy.NEXT_SAFE_PAUSE,
            priority=1,
            detail_code="completed",
            entity_ids=("paper:one",),
            artifact_ids=("artifact:report",),
            occurred_at=datetime(2026, 7, 18, 12, tzinfo=UTC),
        )
    )

    state = protocol_pb2.ServerEvent.FromString(transport.packets[0])
    ready = protocol_pb2.ServerEvent.FromString(transport.packets[1])
    message = protocol_pb2.ServerEvent.FromString(transport.packets[2])
    playback = protocol_pb2.ServerEvent.FromString(transport.packets[3])
    job = protocol_pb2.ServerEvent.FromString(transport.packets[4])
    assert state.state_changed.current == protocol_pb2.CONVERSATION_STATE_THINKING
    assert state.state_changed.reason == "transcription complete"
    assert ready.session_ready.session_id == "s1"
    assert ready.session_ready.generation == 2
    assert message.message.content.inline_text == "hello"
    assert playback.playback_segment.playback_id == "playback-1"
    assert playback.playback_segment.segment_id == "segment-1"
    assert playback.playback_segment.duration_ms == 1250
    assert playback.playback_segment.source == protocol_pb2.SOURCE_KIND_TEXT_SOURCE
    assert playback.playback_segment.speech_rate_multiplier == 1.25
    assert playback.playback_segment.operation.trace_id == "trc_" + "a" * 32
    assert job.job_event.event_id == "job-event:job-1:3"
    assert job.job_event.delivery_sequence == 9
    assert job.job_event.job.session_id == "s1"
    assert job.job_event.job.status == protocol_pb2.JOB_STATUS_SUCCEEDED
    assert job.job_event.kind == protocol_pb2.JOB_EVENT_KIND_SUCCEEDED
    assert job.job_event.notification_policy == (
        protocol_pb2.JOB_NOTIFICATION_POLICY_NEXT_SAFE_PAUSE
    )
    assert list(job.job_event.entity_ids) == ["paper:one"]
    assert list(job.job_event.artifact_ids) == ["artifact:report"]


@pytest.mark.asyncio
async def test_interrupted_state_keeps_the_cancelled_operation_generation() -> None:
    transport = FakeTransport()
    sink = LiveKitEventSink(transport, "s1")  # type: ignore[arg-type]
    interrupted = Operation(
        "s1",
        4,
        "operation-4",
        "cancel-4",
        "trc_" + "b" * 32,
    )

    await sink.state_changed(
        ConversationState.SPEAKING,
        ConversationState.LISTENING,
        5,
        "interrupted",
        interrupted,
    )

    event = protocol_pb2.ServerEvent.FromString(transport.packets[0])
    assert event.state_changed.operation.generation == 4
    assert event.state_changed.operation.operation_id == "operation-4"
