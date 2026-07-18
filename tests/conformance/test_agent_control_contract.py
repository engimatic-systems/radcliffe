from __future__ import annotations

from datetime import UTC, datetime, timedelta

from google.protobuf.timestamp_pb2 import Timestamp
from radcliffe_domain.agent_control import (
    AgentControlBoundary,
    AgentControlGrant,
)
from radcliffe_protocol.radcliffe.v2 import protocol_pb2

NOW = datetime(2026, 7, 18, tzinfo=UTC)


def request(
    *,
    request_id: str,
    idempotency_key: str,
    sequence: int,
    rate: int = protocol_pb2.SPEECH_RATE_NORMAL_1_0X,
    generation: int = 4,
) -> protocol_pb2.AgentControlActionRequest:
    occurred_at = Timestamp()
    occurred_at.FromDatetime(NOW)
    return protocol_pb2.AgentControlActionRequest(
        schema_version=1,
        request_id=request_id,
        operation=protocol_pb2.OperationRef(
            session_id="conformance-session",
            generation=generation,
            operation_id="conformance-operation",
            cancellation_id="conformance-cancellation",
            trace_id=f"trc_{'b' * 32}",
        ),
        origin=protocol_pb2.AgentControlOrigin(
            source=protocol_pb2.SOURCE_KIND_CALL,
            actor_id="conformance-agent",
            source_message_id="conformance-message",
            tool_call_id="conformance-call",
        ),
        target=protocol_pb2.AgentControlTarget(
            scope=protocol_pb2.AGENT_CONTROL_SCOPE_SNIPPET,
            session_id="conformance-session",
            turn_id="conformance-turn",
            snippet_id="conformance-snippet",
        ),
        action_id=protocol_pb2.AGENT_CONTROL_ACTION_ID_SPEECH_DELIVERY_SET_RATE,
        request_sequence=sequence,
        idempotency_key=idempotency_key,
        confirmation=protocol_pb2.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_NONE,
        speech_delivery=protocol_pb2.SpeechDeliveryAction(rate=rate),
        requested_at=occurred_at,
    )


def grant(*, actor_id: str = "conformance-agent", generation: int = 4) -> AgentControlGrant:
    return AgentControlGrant(
        grant_id="conformance-grant",
        session_id="conformance-session",
        generation=generation,
        actor_id=actor_id,
        allowed_origins=frozenset({protocol_pb2.SOURCE_KIND_CALL}),
        allowed_actions=frozenset({protocol_pb2.AGENT_CONTROL_ACTION_ID_SPEECH_DELIVERY_SET_RATE}),
        allowed_scopes=frozenset({protocol_pb2.AGENT_CONTROL_SCOPE_SNIPPET}),
        expires_at=NOW + timedelta(minutes=1),
    )


def test_action_boundary_conformance_matrix_is_deterministic_and_fail_closed() -> None:
    valid_boundary = AgentControlBoundary(clock=lambda: NOW)
    valid = request(request_id="valid", idempotency_key="valid", sequence=1)
    accepted = valid_boundary.submit(valid, grant=grant(), current_generation=4)
    replayed = valid_boundary.submit(valid, grant=grant(), current_generation=4)
    changed_replay = request(
        request_id="changed",
        idempotency_key="valid",
        sequence=1,
        rate=protocol_pb2.SPEECH_RATE_FAST_1_25X,
    )
    idempotency_conflict = valid_boundary.submit(
        changed_replay,
        grant=grant(),
        current_generation=4,
    )

    collision_boundary = AgentControlBoundary(clock=lambda: NOW)
    collision_boundary.submit(
        request(request_id="slot-a", idempotency_key="slot-a", sequence=2),
        grant=grant(),
        current_generation=4,
    )
    collision = collision_boundary.submit(
        request(
            request_id="slot-b",
            idempotency_key="slot-b",
            sequence=2,
            rate=protocol_pb2.SPEECH_RATE_FAST_1_25X,
        ),
        grant=grant(),
        current_generation=4,
    )

    malformed = request(request_id="malformed", idempotency_key="malformed", sequence=3)
    malformed.schema_version = 2
    malformed_result = AgentControlBoundary(clock=lambda: NOW).submit(
        malformed,
        grant=grant(),
        current_generation=4,
    )
    unauthorized_result = AgentControlBoundary(clock=lambda: NOW).submit(
        request(request_id="unauthorized", idempotency_key="unauthorized", sequence=4),
        grant=grant(actor_id="another-agent"),
        current_generation=4,
    )
    stale_result = AgentControlBoundary(clock=lambda: NOW).submit(
        request(
            request_id="stale",
            idempotency_key="stale",
            sequence=5,
            generation=3,
        ),
        grant=grant(generation=3),
        current_generation=4,
    )
    unknown = request(request_id="future", idempotency_key="future", sequence=6)
    unknown.action_id = 99
    unknown_result = AgentControlBoundary(clock=lambda: NOW).submit(
        unknown,
        grant=grant(),
        current_generation=4,
    )

    assert accepted.result.disposition == protocol_pb2.AGENT_CONTROL_DISPOSITION_AUTHORIZED
    assert replayed.replayed and replayed.result == accepted.result
    assert idempotency_conflict.result.rejection_code == (
        protocol_pb2.AGENT_CONTROL_REJECTION_CODE_IDEMPOTENCY_CONFLICT
    )
    assert collision.result.rejection_code == protocol_pb2.AGENT_CONTROL_REJECTION_CODE_CONFLICT
    assert malformed_result.result.rejection_code == (
        protocol_pb2.AGENT_CONTROL_REJECTION_CODE_UNSUPPORTED_SCHEMA_VERSION
    )
    assert unauthorized_result.result.rejection_code == (
        protocol_pb2.AGENT_CONTROL_REJECTION_CODE_UNAUTHORIZED
    )
    assert stale_result.result.rejection_code == (
        protocol_pb2.AGENT_CONTROL_REJECTION_CODE_STALE_GENERATION
    )
    assert unknown_result.result.rejection_code == (
        protocol_pb2.AGENT_CONTROL_REJECTION_CODE_UNREGISTERED_ACTION
    )


def test_action_schema_has_no_generic_escape_hatch() -> None:
    forbidden = {
        "arguments_json",
        "command",
        "credential",
        "environment",
        "path",
        "process",
        "secret",
        "setting_name",
        "url",
    }
    messages = (
        protocol_pb2.AgentControlActionRequest,
        protocol_pb2.SpeechDeliveryAction,
        protocol_pb2.ConfigurationAction,
        protocol_pb2.SessionCreateAction,
        protocol_pb2.SessionSwitchAction,
        protocol_pb2.SessionRestartAction,
    )

    observed = {field.name for message in messages for field in message.DESCRIPTOR.fields}

    assert forbidden.isdisjoint(observed)
    assert set(protocol_pb2.AgentConfigurationKey.keys()) == {
        "AGENT_CONFIGURATION_KEY_UNSPECIFIED",
        "AGENT_CONFIGURATION_KEY_RESPONSE_DETAIL",
        "AGENT_CONFIGURATION_KEY_DEFAULT_SPEECH_RATE",
    }
