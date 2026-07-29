from radcliffe_protocol.radcliffe.v2.protocol_pb2 import (
    AGENT_CONTROL_ACTION_ID_SPEECH_DELIVERY_SET_RATE,
    AGENT_CONTROL_CONFIRMATION_REQUIREMENT_NONE,
    AGENT_CONTROL_SCOPE_SNIPPET,
    JOB_EVENT_KIND_SUCCEEDED,
    JOB_NOTIFICATION_POLICY_NEXT_SAFE_PAUSE,
    JOB_STATUS_SUCCEEDED,
    PLAYBACK_RECONCILIATION_OUTCOME_READY,
    PLAYBACK_STATUS_ACCEPTED,
    SENSITIVITY_PRIVATE,
    SOURCE_KIND_USER,
    SPEECH_RATE_NORMAL_1_0X,
    AgentControlActionRequest,
    AgentControlOrigin,
    AgentControlTarget,
    ClientEvent,
    ContentRef,
    JobEvent,
    JobRef,
    MessageEnvelope,
    OperationRef,
    PlaybackAcknowledgement,
    PlaybackReconciled,
    PlaybackSegment,
    Reconnect,
    ServerEvent,
    SpeechDeliveryAction,
)


def test_message_envelope_round_trip_preserves_identity_and_unknown_safe_shape() -> None:
    envelope = MessageEnvelope(
        schema_version=1,
        message_id="msg-1",
        session_id="session-1",
        sequence=1,
        source=SOURCE_KIND_USER,
        content=ContentRef(mime_type="text/plain", byte_length=5, inline_text="hello"),
        sensitivity=SENSITIVITY_PRIVATE,
    )
    wire = ClientEvent(protocol_version=2, message=envelope).SerializeToString(deterministic=True)

    parsed = ClientEvent.FromString(wire)

    assert parsed.protocol_version == 2
    assert parsed.WhichOneof("payload") == "message"
    assert parsed.message.message_id == "msg-1"
    assert parsed.message.content.inline_text == "hello"


def test_agent_control_action_round_trip_uses_the_server_event_contract() -> None:
    action = AgentControlActionRequest(
        schema_version=1,
        request_id="request-1",
        operation=OperationRef(
            session_id="session-1",
            generation=2,
            operation_id="operation-1",
            cancellation_id="cancellation-1",
            trace_id=f"trc_{'a' * 32}",
        ),
        origin=AgentControlOrigin(
            source=SOURCE_KIND_USER,
            actor_id="user-1",
            source_message_id="message-1",
        ),
        target=AgentControlTarget(
            scope=AGENT_CONTROL_SCOPE_SNIPPET,
            session_id="session-1",
            turn_id="turn-1",
            snippet_id="snippet-1",
        ),
        action_id=AGENT_CONTROL_ACTION_ID_SPEECH_DELIVERY_SET_RATE,
        request_sequence=1,
        idempotency_key="idempotency-1",
        confirmation=AGENT_CONTROL_CONFIRMATION_REQUIREMENT_NONE,
        speech_delivery=SpeechDeliveryAction(rate=SPEECH_RATE_NORMAL_1_0X),
    )
    wire = ServerEvent(protocol_version=2, agent_control_request=action).SerializeToString(
        deterministic=True
    )

    parsed = ServerEvent.FromString(wire)

    assert parsed.WhichOneof("payload") == "agent_control_request"
    assert parsed.agent_control_request.WhichOneof("action") == "speech_delivery"
    assert parsed.agent_control_request.action_id == (
        AGENT_CONTROL_ACTION_ID_SPEECH_DELIVERY_SET_RATE
    )
    assert parsed.agent_control_request.origin.source == SOURCE_KIND_USER


def test_agent_control_request_preserves_unknown_minor_version_fields() -> None:
    action = AgentControlActionRequest(
        schema_version=1,
        request_id="forward-compatible",
        action_id=AGENT_CONTROL_ACTION_ID_SPEECH_DELIVERY_SET_RATE,
    )
    # Field 127, wire type varint, value 1. Protocol v2 readers must preserve
    # additive fields even though the active action boundary safely rejects an
    # unknown action or incomplete known action.
    unknown_field = b"\xf8\x07\x01"

    parsed = AgentControlActionRequest.FromString(
        action.SerializeToString(deterministic=True) + unknown_field
    )
    forwarded = parsed.SerializeToString(deterministic=True)

    assert forwarded.endswith(unknown_field)


def test_job_event_round_trip_exposes_metadata_without_private_job_context() -> None:
    event = JobEvent(
        schema_version=1,
        event_id="job-event:job-1:4",
        job=JobRef(
            job_id="job-1",
            session_id="session-1",
            revision=4,
            status=JOB_STATUS_SUCCEEDED,
        ),
        kind=JOB_EVENT_KIND_SUCCEEDED,
        notification_policy=JOB_NOTIFICATION_POLICY_NEXT_SAFE_PAUSE,
        priority=1,
        detail_code="completed",
        entity_ids=["paper:one"],
        artifact_ids=["artifact:report"],
    )
    wire = ServerEvent(protocol_version=2, job_event=event).SerializeToString(deterministic=True)

    parsed = ServerEvent.FromString(wire)

    assert parsed.WhichOneof("payload") == "job_event"
    assert parsed.job_event.job.status == JOB_STATUS_SUCCEEDED
    assert parsed.job_event.entity_ids == ["paper:one"]
    assert "instruction" not in str(parsed.job_event).lower()


def test_bounded_playback_credit_identity_round_trips_without_sentinels() -> None:
    operation = OperationRef(
        session_id="session-1",
        generation=3,
        operation_id="operation-1",
        cancellation_id="cancel-1",
        trace_id=f"trc_{'b' * 32}",
    )
    segment = PlaybackSegment(
        operation=operation,
        playback_id="playback-1",
        segment_id="segment-1",
        duration_ms=1_000,
        playback_protocol_revision=1,
        receiver_epoch="receiver-1",
        credit_sequence=4,
        attempt_sequence=9,
    )
    accepted = PlaybackAcknowledgement(
        acknowledgement_id="accepted-1",
        operation=operation,
        playback_id="playback-1",
        segment_id="segment-1",
        status=PLAYBACK_STATUS_ACCEPTED,
        receiver_epoch="receiver-1",
        credit_sequence=4,
        attempt_sequence=9,
    )
    reconnect = Reconnect(
        request_id="reconnect-1",
        session_id="session-1",
        generation=3,
        latest_playback=[accepted],
        max_inflight_attempts=1,
        max_segment_duration_ms=30_000,
        playback_protocol_revision=1,
        receiver_epoch="receiver-1",
        credit_sequence=4,
    )
    reconciled = PlaybackReconciled(
        request_id="reconnect-1",
        session_id="session-1",
        generation=3,
        playback_protocol_revision=1,
        receiver_epoch="receiver-1",
        credit_sequence=4,
        outcome=PLAYBACK_RECONCILIATION_OUTCOME_READY,
    )

    assert PlaybackSegment.FromString(segment.SerializeToString()) == segment
    assert Reconnect.FromString(reconnect.SerializeToString()) == reconnect
    assert PlaybackReconciled.FromString(reconciled.SerializeToString()) == reconciled
