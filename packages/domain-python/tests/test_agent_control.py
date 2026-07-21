from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from google.protobuf.timestamp_pb2 import Timestamp
from radcliffe_domain.agent_control import (
    AgentControlBoundary,
    AgentControlConflictError,
    AgentControlGrant,
    precedence_key,
    select_effective_action,
)
from radcliffe_protocol.radcliffe.v2 import protocol_pb2

NOW = datetime(2026, 7, 18, tzinfo=UTC)


def timestamp() -> Timestamp:
    value = Timestamp()
    value.FromDatetime(NOW)
    return value


def operation(*, generation: int = 3) -> protocol_pb2.OperationRef:
    return protocol_pb2.OperationRef(
        session_id="session-1",
        generation=generation,
        operation_id="operation-1",
        cancellation_id="cancellation-1",
        trace_id=f"trc_{'a' * 32}",
    )


def origin(
    *,
    source: int = protocol_pb2.SOURCE_KIND_CALL,
    actor_id: str = "agent-1",
) -> protocol_pb2.AgentControlOrigin:
    return protocol_pb2.AgentControlOrigin(
        source=source,
        actor_id=actor_id,
        source_message_id="message-1",
        tool_call_id="call-1" if source == protocol_pb2.SOURCE_KIND_CALL else "",
    )


def grant(
    *,
    actor_id: str = "agent-1",
    generation: int = 3,
    origins: frozenset[int] = frozenset(
        {
            protocol_pb2.SOURCE_KIND_USER,
            protocol_pb2.SOURCE_KIND_MODEL,
            protocol_pb2.SOURCE_KIND_CALL,
            protocol_pb2.SOURCE_KIND_TEXT_SOURCE,
            protocol_pb2.SOURCE_KIND_SYSTEM,
        }
    ),
) -> AgentControlGrant:
    return AgentControlGrant(
        grant_id="grant-1",
        session_id="session-1",
        generation=generation,
        actor_id=actor_id,
        allowed_origins=origins,
        allowed_actions=frozenset(
            {
                protocol_pb2.AGENT_CONTROL_ACTION_ID_SPEECH_DELIVERY_SET_RATE,
                protocol_pb2.AGENT_CONTROL_ACTION_ID_CONFIGURATION_SET,
                protocol_pb2.AGENT_CONTROL_ACTION_ID_SESSION_CREATE,
                protocol_pb2.AGENT_CONTROL_ACTION_ID_SESSION_SWITCH,
                protocol_pb2.AGENT_CONTROL_ACTION_ID_SESSION_RESTART,
            }
        ),
        allowed_scopes=frozenset(
            {
                protocol_pb2.AGENT_CONTROL_SCOPE_SNIPPET,
                protocol_pb2.AGENT_CONTROL_SCOPE_TURN,
                protocol_pb2.AGENT_CONTROL_SCOPE_SESSION,
                protocol_pb2.AGENT_CONTROL_SCOPE_DEVICE,
                protocol_pb2.AGENT_CONTROL_SCOPE_OPERATOR_DEFAULT,
            }
        ),
        expires_at=NOW + timedelta(minutes=5),
    )


def delivery_request(
    *,
    request_id: str = "request-1",
    idempotency_key: str = "idempotency-1",
    request_sequence: int = 1,
    rate: int = protocol_pb2.SPEECH_RATE_NORMAL_1_0X,
    rate_multiplier: float = 0.0,
    source: int = protocol_pb2.SOURCE_KIND_CALL,
    actor_id: str = "agent-1",
    generation: int = 3,
) -> protocol_pb2.AgentControlActionRequest:
    return protocol_pb2.AgentControlActionRequest(
        schema_version=1,
        request_id=request_id,
        operation=operation(generation=generation),
        origin=origin(source=source, actor_id=actor_id),
        target=protocol_pb2.AgentControlTarget(
            scope=protocol_pb2.AGENT_CONTROL_SCOPE_SNIPPET,
            session_id="session-1",
            turn_id="turn-1",
            snippet_id="snippet-1",
        ),
        action_id=protocol_pb2.AGENT_CONTROL_ACTION_ID_SPEECH_DELIVERY_SET_RATE,
        request_sequence=request_sequence,
        idempotency_key=idempotency_key,
        confirmation=protocol_pb2.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_NONE,
        speech_delivery=protocol_pb2.SpeechDeliveryAction(
            rate=rate,
            rate_multiplier=rate_multiplier,
        ),
        requested_at=timestamp(),
    )


def configuration_request(
    *,
    request_id: str,
    idempotency_key: str,
    scope: int,
    sequence: int,
    confirmation: int,
) -> protocol_pb2.AgentControlActionRequest:
    target = protocol_pb2.AgentControlTarget(scope=scope, session_id="session-1")
    if scope == protocol_pb2.AGENT_CONTROL_SCOPE_TURN:
        target.turn_id = "turn-1"
    elif scope == protocol_pb2.AGENT_CONTROL_SCOPE_DEVICE:
        target.device_id = "pixel-1"
    return protocol_pb2.AgentControlActionRequest(
        schema_version=1,
        request_id=request_id,
        operation=operation(),
        origin=origin(),
        target=target,
        action_id=protocol_pb2.AGENT_CONTROL_ACTION_ID_CONFIGURATION_SET,
        request_sequence=sequence,
        idempotency_key=idempotency_key,
        confirmation=confirmation,
        configuration=protocol_pb2.ConfigurationAction(
            key=protocol_pb2.AGENT_CONFIGURATION_KEY_RESPONSE_DETAIL,
            response_detail=protocol_pb2.RESPONSE_DETAIL_BRIEF,
        ),
        requested_at=timestamp(),
    )


def confirmation(
    request: protocol_pb2.AgentControlActionRequest,
    *,
    approved: bool = True,
    actor_id: str = "user-1",
    key: str = "confirmation-key-1",
) -> protocol_pb2.AgentControlConfirmation:
    return protocol_pb2.AgentControlConfirmation(
        schema_version=1,
        confirmation_id="confirmation-1",
        request_id=request.request_id,
        operation=request.operation,
        origin=origin(source=protocol_pb2.SOURCE_KIND_USER, actor_id=actor_id),
        approved=approved,
        idempotency_key=key,
        occurred_at=timestamp(),
    )


def cancellation(
    request_id: str,
    *,
    actor_id: str = "user-1",
    key: str = "cancellation-key-1",
) -> protocol_pb2.AgentControlCancellation:
    return protocol_pb2.AgentControlCancellation(
        schema_version=1,
        cancellation_id="cancel-action-1",
        request_id=request_id,
        operation=operation(),
        origin=origin(source=protocol_pb2.SOURCE_KIND_USER, actor_id=actor_id),
        idempotency_key=key,
        occurred_at=timestamp(),
    )


def boundary() -> AgentControlBoundary:
    return AgentControlBoundary(clock=lambda: NOW)


def test_registered_typed_delivery_is_authorized_and_content_free_auditable() -> None:
    action = delivery_request()
    decision = boundary().submit(action, grant=grant(), current_generation=3)

    assert decision.result.disposition == protocol_pb2.AGENT_CONTROL_DISPOSITION_AUTHORIZED
    assert decision.result.rejection_code == 0
    assert len(decision.result.request_sha256) == 32
    audit = boundary().audit_fields(action, decision)
    assert audit["action_id"] == (protocol_pb2.AGENT_CONTROL_ACTION_ID_SPEECH_DELIVERY_SET_RATE)
    assert audit["origin"] == protocol_pb2.SOURCE_KIND_CALL
    assert "rate" not in audit


@pytest.mark.parametrize("rate_multiplier", [0.75, 0.9, 1.0, 1.333, 1.5])
def test_bounded_numeric_delivery_rate_is_authorized(rate_multiplier: float) -> None:
    action = delivery_request(rate=0, rate_multiplier=rate_multiplier)

    decision = boundary().submit(action, grant=grant(), current_generation=3)

    assert decision.result.disposition == protocol_pb2.AGENT_CONTROL_DISPOSITION_AUTHORIZED


@pytest.mark.parametrize("rate_multiplier", [float("nan"), 0.749, 1.501])
def test_unbounded_numeric_delivery_rate_is_rejected(rate_multiplier: float) -> None:
    action = delivery_request(rate=0, rate_multiplier=rate_multiplier)

    decision = boundary().submit(action, grant=grant(), current_generation=3)

    assert decision.result.rejection_code == (
        protocol_pb2.AGENT_CONTROL_REJECTION_CODE_INVALID_ARGUMENT
    )


def test_named_and_numeric_delivery_rates_are_mutually_exclusive() -> None:
    action = delivery_request(rate_multiplier=1.25)

    decision = boundary().submit(action, grant=grant(), current_generation=3)

    assert decision.result.rejection_code == (
        protocol_pb2.AGENT_CONTROL_REJECTION_CODE_INVALID_ARGUMENT
    )


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda item: setattr(item, "schema_version", 2),
            protocol_pb2.AGENT_CONTROL_REJECTION_CODE_UNSUPPORTED_SCHEMA_VERSION,
        ),
        (
            lambda item: setattr(item.operation, "trace_id", "unsafe"),
            protocol_pb2.AGENT_CONTROL_REJECTION_CODE_MALFORMED,
        ),
        (
            lambda item: setattr(item.target, "session_id", "another-session"),
            protocol_pb2.AGENT_CONTROL_REJECTION_CODE_INVALID_TARGET,
        ),
        (
            lambda item: setattr(item.speech_delivery, "rate", 0),
            protocol_pb2.AGENT_CONTROL_REJECTION_CODE_INVALID_ARGUMENT,
        ),
        (
            lambda item: setattr(item, "confirmation", 0),
            protocol_pb2.AGENT_CONTROL_REJECTION_CODE_CONFIRMATION_POLICY_MISMATCH,
        ),
    ],
)
def test_malformed_requests_receive_stable_safe_rejection_codes(mutate, expected_code) -> None:
    action = delivery_request()
    mutate(action)

    decision = boundary().submit(action, grant=grant(), current_generation=3)

    assert decision.result.disposition == protocol_pb2.AGENT_CONTROL_DISPOSITION_REJECTED
    assert decision.result.rejection_code == expected_code
    assert not decision.result.retryable


def test_unknown_actions_and_arbitrary_configuration_keys_are_not_registered() -> None:
    unknown = delivery_request(request_id="unknown", idempotency_key="unknown")
    unknown.action_id = 99
    unknown_decision = boundary().submit(unknown, grant=grant(), current_generation=3)

    arbitrary = configuration_request(
        request_id="arbitrary",
        idempotency_key="arbitrary",
        scope=protocol_pb2.AGENT_CONTROL_SCOPE_SESSION,
        sequence=2,
        confirmation=protocol_pb2.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_NONE,
    )
    arbitrary.configuration.key = 99
    arbitrary_decision = boundary().submit(arbitrary, grant=grant(), current_generation=3)

    assert unknown_decision.result.rejection_code == (
        protocol_pb2.AGENT_CONTROL_REJECTION_CODE_UNREGISTERED_ACTION
    )
    assert arbitrary_decision.result.rejection_code == (
        protocol_pb2.AGENT_CONTROL_REJECTION_CODE_INVALID_ARGUMENT
    )


def test_authorization_and_generation_are_checked_before_action_release() -> None:
    action = delivery_request()
    unauthorized = boundary().submit(
        action,
        grant=grant(actor_id="another-actor"),
        current_generation=3,
    )
    stale = boundary().submit(
        delivery_request(generation=2),
        grant=grant(generation=2),
        current_generation=3,
    )

    assert unauthorized.result.rejection_code == (
        protocol_pb2.AGENT_CONTROL_REJECTION_CODE_UNAUTHORIZED
    )
    assert stale.result.rejection_code == (
        protocol_pb2.AGENT_CONTROL_REJECTION_CODE_STALE_GENERATION
    )


def test_exact_replay_returns_first_result_and_changed_replay_is_rejected() -> None:
    evaluator = boundary()
    action = delivery_request()
    first = evaluator.submit(action, grant=grant(), current_generation=3)
    replay = evaluator.submit(action, grant=grant(), current_generation=3)
    changed = delivery_request(rate=protocol_pb2.SPEECH_RATE_FAST_1_25X)
    conflict = evaluator.submit(changed, grant=grant(), current_generation=3)

    assert replay.replayed
    assert replay.result == first.result
    assert conflict.result.rejection_code == (
        protocol_pb2.AGENT_CONTROL_REJECTION_CODE_IDEMPOTENCY_CONFLICT
    )


def test_same_precedence_slot_with_different_requests_is_a_conflict() -> None:
    evaluator = boundary()
    first = delivery_request()
    second = delivery_request(
        request_id="request-2",
        idempotency_key="idempotency-2",
        rate=protocol_pb2.SPEECH_RATE_FAST_1_25X,
    )

    assert evaluator.submit(first, grant=grant(), current_generation=3).result.disposition == (
        protocol_pb2.AGENT_CONTROL_DISPOSITION_AUTHORIZED
    )
    rejected = evaluator.submit(second, grant=grant(), current_generation=3)

    assert rejected.result.rejection_code == protocol_pb2.AGENT_CONTROL_REJECTION_CODE_CONFLICT


def test_precedence_is_snippet_turn_session_device_operator_then_sequence() -> None:
    operator = configuration_request(
        request_id="operator",
        idempotency_key="operator",
        scope=protocol_pb2.AGENT_CONTROL_SCOPE_OPERATOR_DEFAULT,
        sequence=1,
        confirmation=protocol_pb2.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_OPERATOR,
    )
    device = configuration_request(
        request_id="device",
        idempotency_key="device",
        scope=protocol_pb2.AGENT_CONTROL_SCOPE_DEVICE,
        sequence=2,
        confirmation=protocol_pb2.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_USER,
    )
    session = configuration_request(
        request_id="session",
        idempotency_key="session",
        scope=protocol_pb2.AGENT_CONTROL_SCOPE_SESSION,
        sequence=3,
        confirmation=protocol_pb2.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_NONE,
    )
    turn = configuration_request(
        request_id="turn",
        idempotency_key="turn",
        scope=protocol_pb2.AGENT_CONTROL_SCOPE_TURN,
        sequence=4,
        confirmation=protocol_pb2.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_NONE,
    )

    assert select_effective_action((turn, operator, session, device)) == turn
    assert precedence_key(operator) < precedence_key(device) < precedence_key(session)
    assert precedence_key(session) < precedence_key(turn)

    conflicting = protocol_pb2.AgentControlActionRequest()
    conflicting.CopyFrom(turn)
    conflicting.request_id = "turn-conflict"
    conflicting.idempotency_key = "turn-conflict"
    conflicting.configuration.response_detail = protocol_pb2.RESPONSE_DETAIL_DETAILED
    with pytest.raises(AgentControlConflictError, match="same precedence slot"):
        select_effective_action((turn, conflicting))


def test_user_confirmation_authorizes_device_change_and_replays_exactly() -> None:
    evaluator = boundary()
    action = configuration_request(
        request_id="device-setting",
        idempotency_key="device-setting",
        scope=protocol_pb2.AGENT_CONTROL_SCOPE_DEVICE,
        sequence=1,
        confirmation=protocol_pb2.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_USER,
    )
    pending = evaluator.submit(action, grant=grant(), current_generation=3)
    approval = confirmation(action)
    user_grant = grant(actor_id="user-1")
    authorized = evaluator.confirm(approval, grant=user_grant, current_generation=3)
    replay = evaluator.confirm(approval, grant=user_grant, current_generation=3)

    assert pending.result.disposition == (
        protocol_pb2.AGENT_CONTROL_DISPOSITION_AWAITING_CONFIRMATION
    )
    assert authorized.result.disposition == protocol_pb2.AGENT_CONTROL_DISPOSITION_AUTHORIZED
    assert replay.replayed
    assert replay.result == authorized.result
    assert evaluator.submit(action, grant=grant(), current_generation=3).result == (
        authorized.result
    )


def test_pending_action_can_be_cancelled_and_authorized_action_cannot_be_rolled_back() -> None:
    evaluator = boundary()
    pending_action = configuration_request(
        request_id="pending",
        idempotency_key="pending",
        scope=protocol_pb2.AGENT_CONTROL_SCOPE_DEVICE,
        sequence=1,
        confirmation=protocol_pb2.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_USER,
    )
    evaluator.submit(pending_action, grant=grant(), current_generation=3)
    cancelled = evaluator.cancel(
        cancellation("pending"),
        grant=grant(actor_id="user-1"),
        current_generation=3,
    )

    applied_action = delivery_request(
        request_id="authorized",
        idempotency_key="authorized",
        request_sequence=2,
    )
    evaluator.submit(applied_action, grant=grant(), current_generation=3)
    too_late = evaluator.cancel(
        cancellation("authorized", key="cancel-authorized"),
        grant=grant(actor_id="user-1"),
        current_generation=3,
    )

    assert cancelled.result.disposition == protocol_pb2.AGENT_CONTROL_DISPOSITION_CANCELLED
    assert cancelled.result.rejection_code == (protocol_pb2.AGENT_CONTROL_REJECTION_CODE_CANCELLED)
    assert too_late.result.rejection_code == (
        protocol_pb2.AGENT_CONTROL_REJECTION_CODE_ALREADY_APPLIED
    )


def test_cancellation_tombstone_wins_if_it_arrives_before_request() -> None:
    evaluator = boundary()
    user_grant = grant(actor_id="user-1")
    cancelled = evaluator.cancel(
        cancellation("future-request"),
        grant=user_grant,
        current_generation=3,
    )
    future = delivery_request(
        request_id="future-request",
        idempotency_key="future-request",
        request_sequence=5,
    )
    result = evaluator.submit(future, grant=grant(), current_generation=3)

    assert cancelled.result.disposition == protocol_pb2.AGENT_CONTROL_DISPOSITION_CANCELLED
    assert result.result.disposition == protocol_pb2.AGENT_CONTROL_DISPOSITION_CANCELLED


@pytest.mark.parametrize(
    "source",
    [
        protocol_pb2.SOURCE_KIND_USER,
        protocol_pb2.SOURCE_KIND_MODEL,
        protocol_pb2.SOURCE_KIND_CALL,
        protocol_pb2.SOURCE_KIND_TEXT_SOURCE,
    ],
)
def test_delivery_origin_remains_distinguishable(source: int) -> None:
    action = delivery_request(source=source)
    decision = boundary().submit(action, grant=grant(), current_generation=3)

    assert decision.result.disposition == protocol_pb2.AGENT_CONTROL_DISPOSITION_AUTHORIZED
    assert action.origin.source == source
