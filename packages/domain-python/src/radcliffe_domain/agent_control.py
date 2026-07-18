from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import cast

from google.protobuf.message import Message
from google.protobuf.timestamp_pb2 import Timestamp
from radcliffe_protocol.radcliffe.v2 import protocol_pb2

ACTION_SCHEMA_VERSION = 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TRACE_ID = re.compile(r"^trc_[0-9a-f]{32}$")

_SOURCE_USER = protocol_pb2.SOURCE_KIND_USER
_SOURCE_MODEL = protocol_pb2.SOURCE_KIND_MODEL
_SOURCE_CALL = protocol_pb2.SOURCE_KIND_CALL
_SOURCE_TEXT = protocol_pb2.SOURCE_KIND_TEXT_SOURCE
_SOURCE_SYSTEM = protocol_pb2.SOURCE_KIND_SYSTEM

_SCOPE_SNIPPET = protocol_pb2.AGENT_CONTROL_SCOPE_SNIPPET
_SCOPE_TURN = protocol_pb2.AGENT_CONTROL_SCOPE_TURN
_SCOPE_SESSION = protocol_pb2.AGENT_CONTROL_SCOPE_SESSION
_SCOPE_DEVICE = protocol_pb2.AGENT_CONTROL_SCOPE_DEVICE
_SCOPE_OPERATOR = protocol_pb2.AGENT_CONTROL_SCOPE_OPERATOR_DEFAULT

_CONFIRM_NONE = protocol_pb2.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_NONE
_CONFIRM_USER = protocol_pb2.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_USER
_CONFIRM_OPERATOR = protocol_pb2.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_OPERATOR

_ACTION_DELIVERY = protocol_pb2.AGENT_CONTROL_ACTION_ID_SPEECH_DELIVERY_SET_RATE
_ACTION_CONFIGURATION = protocol_pb2.AGENT_CONTROL_ACTION_ID_CONFIGURATION_SET
_ACTION_SESSION_CREATE = protocol_pb2.AGENT_CONTROL_ACTION_ID_SESSION_CREATE
_ACTION_SESSION_SWITCH = protocol_pb2.AGENT_CONTROL_ACTION_ID_SESSION_SWITCH
_ACTION_SESSION_RESTART = protocol_pb2.AGENT_CONTROL_ACTION_ID_SESSION_RESTART

_DISPOSITION_AWAITING = protocol_pb2.AGENT_CONTROL_DISPOSITION_AWAITING_CONFIRMATION
_DISPOSITION_AUTHORIZED = protocol_pb2.AGENT_CONTROL_DISPOSITION_AUTHORIZED
_DISPOSITION_REJECTED = protocol_pb2.AGENT_CONTROL_DISPOSITION_REJECTED
_DISPOSITION_CANCELLED = protocol_pb2.AGENT_CONTROL_DISPOSITION_CANCELLED

_REJECTION_UNSPECIFIED = protocol_pb2.AGENT_CONTROL_REJECTION_CODE_UNSPECIFIED
_REJECTION_SCHEMA = protocol_pb2.AGENT_CONTROL_REJECTION_CODE_UNSUPPORTED_SCHEMA_VERSION
_REJECTION_MALFORMED = protocol_pb2.AGENT_CONTROL_REJECTION_CODE_MALFORMED
_REJECTION_UNREGISTERED = protocol_pb2.AGENT_CONTROL_REJECTION_CODE_UNREGISTERED_ACTION
_REJECTION_UNAUTHORIZED = protocol_pb2.AGENT_CONTROL_REJECTION_CODE_UNAUTHORIZED
_REJECTION_SCOPE = protocol_pb2.AGENT_CONTROL_REJECTION_CODE_INVALID_SCOPE
_REJECTION_TARGET = protocol_pb2.AGENT_CONTROL_REJECTION_CODE_INVALID_TARGET
_REJECTION_ARGUMENT = protocol_pb2.AGENT_CONTROL_REJECTION_CODE_INVALID_ARGUMENT
_REJECTION_STALE = protocol_pb2.AGENT_CONTROL_REJECTION_CODE_STALE_GENERATION
_REJECTION_IDEMPOTENCY = protocol_pb2.AGENT_CONTROL_REJECTION_CODE_IDEMPOTENCY_CONFLICT
_REJECTION_CONFLICT = protocol_pb2.AGENT_CONTROL_REJECTION_CODE_CONFLICT
_REJECTION_CONFIRMATION = protocol_pb2.AGENT_CONTROL_REJECTION_CODE_CONFIRMATION_POLICY_MISMATCH
_REJECTION_CANCELLED = protocol_pb2.AGENT_CONTROL_REJECTION_CODE_CANCELLED
_REJECTION_ALREADY_APPLIED = protocol_pb2.AGENT_CONTROL_REJECTION_CODE_ALREADY_APPLIED

_SCOPE_PRECEDENCE = MappingProxyType(
    {
        _SCOPE_OPERATOR: 0,
        _SCOPE_DEVICE: 1,
        _SCOPE_SESSION: 2,
        _SCOPE_TURN: 3,
        _SCOPE_SNIPPET: 4,
    }
)


class AgentControlConflictError(ValueError):
    pass


class _ContractRejection(ValueError):
    def __init__(self, code: int) -> None:
        super().__init__(str(code))
        self.code = code


@dataclass(frozen=True, slots=True)
class ActionPolicy:
    action_id: int
    payload_name: str
    allowed_scopes: frozenset[int]
    allowed_origins: frozenset[int]
    confirmation_by_scope: Mapping[int, int]

    def confirmation_for(self, scope: int) -> int:
        requirement = self.confirmation_by_scope.get(scope)
        if requirement is None:
            raise _ContractRejection(_REJECTION_SCOPE)
        return requirement


ACTION_POLICIES: Mapping[int, ActionPolicy] = MappingProxyType(
    {
        _ACTION_DELIVERY: ActionPolicy(
            _ACTION_DELIVERY,
            "speech_delivery",
            frozenset({_SCOPE_SNIPPET}),
            frozenset({_SOURCE_USER, _SOURCE_MODEL, _SOURCE_CALL, _SOURCE_TEXT}),
            MappingProxyType({_SCOPE_SNIPPET: _CONFIRM_NONE}),
        ),
        _ACTION_CONFIGURATION: ActionPolicy(
            _ACTION_CONFIGURATION,
            "configuration",
            frozenset({_SCOPE_TURN, _SCOPE_SESSION, _SCOPE_DEVICE, _SCOPE_OPERATOR}),
            frozenset({_SOURCE_USER, _SOURCE_MODEL, _SOURCE_CALL, _SOURCE_SYSTEM}),
            MappingProxyType(
                {
                    _SCOPE_TURN: _CONFIRM_NONE,
                    _SCOPE_SESSION: _CONFIRM_NONE,
                    _SCOPE_DEVICE: _CONFIRM_USER,
                    _SCOPE_OPERATOR: _CONFIRM_OPERATOR,
                }
            ),
        ),
        _ACTION_SESSION_CREATE: ActionPolicy(
            _ACTION_SESSION_CREATE,
            "session_create",
            frozenset({_SCOPE_SESSION}),
            frozenset({_SOURCE_USER, _SOURCE_MODEL, _SOURCE_CALL}),
            MappingProxyType({_SCOPE_SESSION: _CONFIRM_NONE}),
        ),
        _ACTION_SESSION_SWITCH: ActionPolicy(
            _ACTION_SESSION_SWITCH,
            "session_switch",
            frozenset({_SCOPE_SESSION}),
            frozenset({_SOURCE_USER, _SOURCE_MODEL, _SOURCE_CALL}),
            MappingProxyType({_SCOPE_SESSION: _CONFIRM_USER}),
        ),
        _ACTION_SESSION_RESTART: ActionPolicy(
            _ACTION_SESSION_RESTART,
            "session_restart",
            frozenset({_SCOPE_SESSION}),
            frozenset({_SOURCE_USER, _SOURCE_MODEL, _SOURCE_CALL}),
            MappingProxyType({_SCOPE_SESSION: _CONFIRM_USER}),
        ),
    }
)


@dataclass(frozen=True, slots=True)
class AgentControlGrant:
    grant_id: str
    session_id: str
    generation: int
    actor_id: str
    allowed_origins: frozenset[int]
    allowed_actions: frozenset[int]
    allowed_scopes: frozenset[int]
    expires_at: datetime
    revoked: bool = False


@dataclass(frozen=True, slots=True)
class AgentControlDecision:
    result: protocol_pb2.AgentControlActionResult
    replayed: bool = False


@dataclass(slots=True)
class _ActionRecord:
    request: protocol_pb2.AgentControlActionRequest
    digest: bytes
    decision: AgentControlDecision


def _clone[MessageType: Message](value: MessageType) -> MessageType:
    cloned = type(value)()
    cloned.CopyFrom(value)
    return cloned


def _digest(value: Message) -> bytes:
    return hashlib.sha256(value.SerializeToString(deterministic=True)).digest()


def _safe_identifier(value: str) -> bool:
    return _IDENTIFIER.fullmatch(value) is not None


def _require_identifier(value: str) -> None:
    if not _safe_identifier(value):
        raise _ContractRejection(_REJECTION_MALFORMED)


def _require_empty(*values: str) -> None:
    if any(values):
        raise _ContractRejection(_REJECTION_TARGET)


def _configuration_key(request: protocol_pb2.AgentControlActionRequest) -> int:
    if request.WhichOneof("action") != "configuration":
        return 0
    return request.configuration.key


def precedence_key(request: protocol_pb2.AgentControlActionRequest) -> tuple[int, int, int, str]:
    """Order applicable actions from broad defaults to the most specific request."""
    scope_priority = _SCOPE_PRECEDENCE.get(request.target.scope)
    if scope_priority is None:
        raise AgentControlConflictError("action has no registered scope precedence")
    return (
        scope_priority,
        request.operation.generation,
        request.request_sequence,
        request.request_id,
    )


def select_effective_action(
    requests: Iterable[protocol_pb2.AgentControlActionRequest],
) -> protocol_pb2.AgentControlActionRequest:
    """Select one already-authorized action using deterministic scope precedence."""
    candidates = tuple(requests)
    if not candidates:
        raise ValueError("at least one action is required")
    families = {(item.action_id, _configuration_key(item)) for item in candidates}
    if len(families) != 1:
        raise AgentControlConflictError("actions do not control the same registered value")
    occupied: dict[tuple[int, int, int], bytes] = {}
    for item in candidates:
        slot = (item.target.scope, item.operation.generation, item.request_sequence)
        digest = _digest(item)
        existing = occupied.setdefault(slot, digest)
        if existing != digest:
            raise AgentControlConflictError("conflicting actions occupy the same precedence slot")
    return max(candidates, key=precedence_key)


class AgentControlBoundary:
    """Pure, process-local policy boundary for typed agent-control requests."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        policies: Mapping[int, ActionPolicy] = ACTION_POLICIES,
    ) -> None:
        self.clock = clock or (lambda: datetime.now(UTC))
        self.policies = MappingProxyType(dict(policies))
        self._idempotency: dict[str, tuple[bytes, AgentControlDecision]] = {}
        self._records: dict[str, _ActionRecord] = {}
        self._slots: dict[tuple[object, ...], bytes] = {}
        self._cancelled_request_ids: set[str] = set()
        self._confirmation_idempotency: dict[str, tuple[bytes, AgentControlDecision]] = {}
        self._cancellation_idempotency: dict[str, tuple[bytes, AgentControlDecision]] = {}

    def submit(
        self,
        request: protocol_pb2.AgentControlActionRequest,
        *,
        grant: AgentControlGrant,
        current_generation: int,
    ) -> AgentControlDecision:
        captured = _clone(request)
        digest = _digest(captured)
        if _safe_identifier(captured.idempotency_key):
            existing = self._idempotency.get(captured.idempotency_key)
            if existing is not None:
                previous_digest, previous_decision = existing
                if previous_digest == digest:
                    return replace(previous_decision, replayed=True)
                return self._decision(
                    captured,
                    digest,
                    _DISPOSITION_REJECTED,
                    _REJECTION_IDEMPOTENCY,
                )
        try:
            policy = self._validate_request(captured)
            self._authorize(captured, policy, grant, current_generation)
            previous_record = self._records.get(captured.request_id)
            if previous_record is not None and previous_record.digest != digest:
                raise _ContractRejection(_REJECTION_CONFLICT)
            if captured.request_id in self._cancelled_request_ids:
                decision = self._decision(
                    captured,
                    digest,
                    _DISPOSITION_CANCELLED,
                    _REJECTION_CANCELLED,
                )
            else:
                slot = self._conflict_slot(captured)
                occupied = self._slots.setdefault(slot, digest)
                if occupied != digest:
                    raise _ContractRejection(_REJECTION_CONFLICT)
                required_confirmation = policy.confirmation_for(captured.target.scope)
                if captured.confirmation != required_confirmation:
                    raise _ContractRejection(_REJECTION_CONFIRMATION)
                disposition = (
                    _DISPOSITION_AUTHORIZED
                    if required_confirmation == _CONFIRM_NONE
                    else _DISPOSITION_AWAITING
                )
                decision = self._decision(
                    captured,
                    digest,
                    disposition,
                    _REJECTION_UNSPECIFIED,
                )
        except _ContractRejection as rejection:
            decision = self._decision(
                captured,
                digest,
                _DISPOSITION_REJECTED,
                rejection.code,
            )
        if _safe_identifier(captured.idempotency_key):
            self._idempotency[captured.idempotency_key] = (digest, decision)
        if _safe_identifier(captured.request_id):
            self._records[captured.request_id] = _ActionRecord(captured, digest, decision)
        return decision

    def confirm(
        self,
        confirmation: protocol_pb2.AgentControlConfirmation,
        *,
        grant: AgentControlGrant,
        current_generation: int,
    ) -> AgentControlDecision:
        captured = _clone(confirmation)
        digest = _digest(captured)
        replay = self._confirmation_idempotency.get(captured.idempotency_key)
        if replay is not None:
            replay_digest, decision = replay
            if replay_digest == digest:
                return replace(decision, replayed=True)
            return self._control_rejection(captured.request_id, _REJECTION_IDEMPOTENCY)
        try:
            self._validate_control_message(captured)
            record = self._records.get(captured.request_id)
            if record is None:
                raise _ContractRejection(_REJECTION_MALFORMED)
            policy = self.policies.get(record.request.action_id)
            assert policy is not None
            self._authorize_origin(
                captured.operation,
                captured.origin,
                record.request.action_id,
                record.request.target.scope,
                grant,
                current_generation,
            )
            if captured.operation != record.request.operation:
                raise _ContractRejection(_REJECTION_STALE)
            requirement = policy.confirmation_for(record.request.target.scope)
            expected_source = _SOURCE_USER if requirement == _CONFIRM_USER else _SOURCE_SYSTEM
            if requirement == _CONFIRM_NONE or captured.origin.source != expected_source:
                raise _ContractRejection(_REJECTION_UNAUTHORIZED)
            if record.decision.result.disposition != _DISPOSITION_AWAITING:
                raise _ContractRejection(_REJECTION_ALREADY_APPLIED)
            disposition = _DISPOSITION_AUTHORIZED if captured.approved else _DISPOSITION_CANCELLED
            rejection = _REJECTION_UNSPECIFIED if captured.approved else _REJECTION_CANCELLED
            decision = self._decision(
                record.request,
                record.digest,
                disposition,
                rejection,
            )
            record.decision = decision
            self._idempotency[record.request.idempotency_key] = (record.digest, decision)
            if not captured.approved:
                self._cancelled_request_ids.add(record.request.request_id)
        except _ContractRejection as rejection:
            decision = self._control_rejection(captured.request_id, rejection.code)
        if _safe_identifier(captured.idempotency_key):
            self._confirmation_idempotency[captured.idempotency_key] = (digest, decision)
        return decision

    def cancel(
        self,
        cancellation: protocol_pb2.AgentControlCancellation,
        *,
        grant: AgentControlGrant,
        current_generation: int,
    ) -> AgentControlDecision:
        captured = _clone(cancellation)
        digest = _digest(captured)
        replay = self._cancellation_idempotency.get(captured.idempotency_key)
        if replay is not None:
            replay_digest, decision = replay
            if replay_digest == digest:
                return replace(decision, replayed=True)
            return self._control_rejection(captured.request_id, _REJECTION_IDEMPOTENCY)
        try:
            self._validate_control_message(captured)
            record = self._records.get(captured.request_id)
            action_id = record.request.action_id if record is not None else 0
            scope = record.request.target.scope if record is not None else _SCOPE_SESSION
            self._authorize_origin(
                captured.operation,
                captured.origin,
                action_id,
                scope,
                grant,
                current_generation,
                allow_unknown_action=record is None,
            )
            if record is None:
                self._cancelled_request_ids.add(captured.request_id)
                decision = self._control_rejection(
                    captured.request_id,
                    _REJECTION_CANCELLED,
                    disposition=_DISPOSITION_CANCELLED,
                )
            elif captured.operation != record.request.operation:
                raise _ContractRejection(_REJECTION_STALE)
            elif record.decision.result.disposition == _DISPOSITION_AUTHORIZED:
                raise _ContractRejection(_REJECTION_ALREADY_APPLIED)
            else:
                self._cancelled_request_ids.add(captured.request_id)
                decision = self._decision(
                    record.request,
                    record.digest,
                    _DISPOSITION_CANCELLED,
                    _REJECTION_CANCELLED,
                )
                record.decision = decision
                self._idempotency[record.request.idempotency_key] = (record.digest, decision)
        except _ContractRejection as rejection:
            decision = self._control_rejection(captured.request_id, rejection.code)
        if _safe_identifier(captured.idempotency_key):
            self._cancellation_idempotency[captured.idempotency_key] = (digest, decision)
        return decision

    def audit_fields(
        self,
        request: protocol_pb2.AgentControlActionRequest,
        decision: AgentControlDecision,
    ) -> Mapping[str, str | int | bool]:
        result = decision.result
        return MappingProxyType(
            {
                "schema_version": request.schema_version,
                "request_id": request.request_id,
                "session_id": request.operation.session_id,
                "generation": request.operation.generation,
                "operation_id": request.operation.operation_id,
                "action_id": request.action_id,
                "scope": request.target.scope,
                "origin": request.origin.source,
                "actor_id": request.origin.actor_id,
                "source_message_id": request.origin.source_message_id,
                "tool_call_id": request.origin.tool_call_id,
                "request_sequence": request.request_sequence,
                "idempotency_key": request.idempotency_key,
                "confirmation": request.confirmation,
                "request_sha256": result.request_sha256.hex(),
                "disposition": result.disposition,
                "rejection_code": result.rejection_code,
                "replayed": decision.replayed,
            }
        )

    def _validate_request(self, request: protocol_pb2.AgentControlActionRequest) -> ActionPolicy:
        if request.schema_version != ACTION_SCHEMA_VERSION:
            raise _ContractRejection(_REJECTION_SCHEMA)
        _require_identifier(request.request_id)
        _require_identifier(request.idempotency_key)
        if request.request_sequence < 1 or not request.HasField("requested_at"):
            raise _ContractRejection(_REJECTION_MALFORMED)
        self._validate_operation(request.operation)
        self._validate_origin(request.origin)
        policy = self.policies.get(request.action_id)
        if policy is None:
            raise _ContractRejection(_REJECTION_UNREGISTERED)
        action_name = request.WhichOneof("action")
        if action_name != policy.payload_name:
            raise _ContractRejection(_REJECTION_ARGUMENT)
        if request.target.scope not in policy.allowed_scopes:
            raise _ContractRejection(_REJECTION_SCOPE)
        if request.origin.source not in policy.allowed_origins:
            raise _ContractRejection(_REJECTION_UNAUTHORIZED)
        self._validate_target(request)
        self._validate_arguments(request)
        return policy

    @staticmethod
    def _validate_operation(operation: protocol_pb2.OperationRef) -> None:
        _require_identifier(operation.session_id)
        _require_identifier(operation.operation_id)
        _require_identifier(operation.cancellation_id)
        if operation.generation < 1 or _TRACE_ID.fullmatch(operation.trace_id) is None:
            raise _ContractRejection(_REJECTION_MALFORMED)

    @staticmethod
    def _validate_origin(origin: protocol_pb2.AgentControlOrigin) -> None:
        if origin.source not in {
            _SOURCE_USER,
            _SOURCE_MODEL,
            _SOURCE_CALL,
            _SOURCE_TEXT,
            _SOURCE_SYSTEM,
        }:
            raise _ContractRejection(_REJECTION_MALFORMED)
        _require_identifier(origin.actor_id)
        _require_identifier(origin.source_message_id)
        if origin.source == _SOURCE_CALL:
            _require_identifier(origin.tool_call_id)
        elif origin.tool_call_id:
            raise _ContractRejection(_REJECTION_MALFORMED)

    @staticmethod
    def _validate_target(request: protocol_pb2.AgentControlActionRequest) -> None:
        target = request.target
        if target.session_id != request.operation.session_id:
            raise _ContractRejection(_REJECTION_TARGET)
        if request.action_id == _ACTION_DELIVERY:
            _require_identifier(target.turn_id)
            _require_identifier(target.snippet_id)
            _require_empty(target.device_id)
            return
        if request.action_id == _ACTION_CONFIGURATION:
            if target.scope == _SCOPE_TURN:
                _require_identifier(target.turn_id)
                _require_empty(target.device_id, target.snippet_id)
            elif target.scope == _SCOPE_DEVICE:
                _require_identifier(target.device_id)
                _require_empty(target.turn_id, target.snippet_id)
            else:
                _require_empty(target.device_id, target.turn_id, target.snippet_id)
            return
        _require_empty(target.device_id, target.turn_id, target.snippet_id)

    @staticmethod
    def _validate_arguments(request: protocol_pb2.AgentControlActionRequest) -> None:
        if request.action_id == _ACTION_DELIVERY:
            named_rate = request.speech_delivery.rate
            multiplier = request.speech_delivery.rate_multiplier
            named_rate_is_valid = named_rate in {
                protocol_pb2.SPEECH_RATE_SLOW_0_75X,
                protocol_pb2.SPEECH_RATE_NORMAL_1_0X,
                protocol_pb2.SPEECH_RATE_FAST_1_25X,
                protocol_pb2.SPEECH_RATE_VERY_FAST_1_5X,
            }
            multiplier_is_valid = math.isfinite(multiplier) and 0.75 <= multiplier <= 1.5
            if named_rate_is_valid == multiplier_is_valid:
                raise _ContractRejection(_REJECTION_ARGUMENT)
            return
        if request.action_id == _ACTION_CONFIGURATION:
            value = request.configuration.WhichOneof("value")
            if request.configuration.key == protocol_pb2.AGENT_CONFIGURATION_KEY_RESPONSE_DETAIL:
                if value != "response_detail" or request.configuration.response_detail not in {
                    protocol_pb2.RESPONSE_DETAIL_BRIEF,
                    protocol_pb2.RESPONSE_DETAIL_STANDARD,
                    protocol_pb2.RESPONSE_DETAIL_DETAILED,
                }:
                    raise _ContractRejection(_REJECTION_ARGUMENT)
                return
            if (
                request.configuration.key
                == protocol_pb2.AGENT_CONFIGURATION_KEY_DEFAULT_SPEECH_RATE
            ):
                if value != "speech_rate" or request.configuration.speech_rate not in {
                    protocol_pb2.SPEECH_RATE_SLOW_0_75X,
                    protocol_pb2.SPEECH_RATE_NORMAL_1_0X,
                    protocol_pb2.SPEECH_RATE_FAST_1_25X,
                    protocol_pb2.SPEECH_RATE_VERY_FAST_1_5X,
                }:
                    raise _ContractRejection(_REJECTION_ARGUMENT)
                return
            raise _ContractRejection(_REJECTION_ARGUMENT)
        if request.action_id == _ACTION_SESSION_SWITCH:
            _require_identifier(request.session_switch.destination_session_id)
            if request.session_switch.destination_session_id == request.operation.session_id:
                raise _ContractRejection(_REJECTION_ARGUMENT)

    def _authorize(
        self,
        request: protocol_pb2.AgentControlActionRequest,
        policy: ActionPolicy,
        grant: AgentControlGrant,
        current_generation: int,
    ) -> None:
        self._authorize_origin(
            request.operation,
            request.origin,
            policy.action_id,
            request.target.scope,
            grant,
            current_generation,
        )

    def _authorize_origin(
        self,
        operation: protocol_pb2.OperationRef,
        origin: protocol_pb2.AgentControlOrigin,
        action_id: int,
        scope: int,
        grant: AgentControlGrant,
        current_generation: int,
        *,
        allow_unknown_action: bool = False,
    ) -> None:
        now = self.clock().astimezone(UTC)
        if operation.generation != current_generation or grant.generation != current_generation:
            raise _ContractRejection(_REJECTION_STALE)
        if grant.revoked or grant.expires_at.astimezone(UTC) <= now:
            raise _ContractRejection(_REJECTION_UNAUTHORIZED)
        if (
            grant.session_id != operation.session_id
            or grant.actor_id != origin.actor_id
            or origin.source not in grant.allowed_origins
            or scope not in grant.allowed_scopes
        ):
            raise _ContractRejection(_REJECTION_UNAUTHORIZED)
        if not allow_unknown_action and action_id not in grant.allowed_actions:
            raise _ContractRejection(_REJECTION_UNAUTHORIZED)

    @classmethod
    def _validate_control_message(
        cls,
        value: protocol_pb2.AgentControlConfirmation | protocol_pb2.AgentControlCancellation,
    ) -> None:
        if value.schema_version != ACTION_SCHEMA_VERSION:
            raise _ContractRejection(_REJECTION_SCHEMA)
        identifier = (
            value.confirmation_id
            if isinstance(value, protocol_pb2.AgentControlConfirmation)
            else value.cancellation_id
        )
        _require_identifier(identifier)
        _require_identifier(value.request_id)
        _require_identifier(value.idempotency_key)
        if not value.HasField("occurred_at"):
            raise _ContractRejection(_REJECTION_MALFORMED)
        cls._validate_operation(value.operation)
        cls._validate_origin(value.origin)

    @staticmethod
    def _conflict_slot(request: protocol_pb2.AgentControlActionRequest) -> tuple[object, ...]:
        return (
            request.action_id,
            _configuration_key(request),
            request.target.scope,
            request.target.session_id,
            request.target.device_id,
            request.target.turn_id,
            request.target.snippet_id,
            request.operation.generation,
            request.request_sequence,
        )

    def _decision(
        self,
        request: protocol_pb2.AgentControlActionRequest,
        digest: bytes,
        disposition: int,
        rejection_code: int,
    ) -> AgentControlDecision:
        occurred_at = Timestamp()
        occurred_at.FromDatetime(self.clock().astimezone(UTC))
        action_id = (
            request.action_id
            if request.action_id in self.policies
            else protocol_pb2.AGENT_CONTROL_ACTION_ID_UNSPECIFIED
        )
        result = protocol_pb2.AgentControlActionResult(
            schema_version=ACTION_SCHEMA_VERSION,
            request_id=request.request_id,
            action_id=action_id,
            disposition=cast(protocol_pb2.AgentControlDisposition, disposition),
            rejection_code=cast(protocol_pb2.AgentControlRejectionCode, rejection_code),
            idempotency_key=request.idempotency_key,
            request_sha256=digest,
            occurred_at=occurred_at,
            retryable=False,
        )
        result.operation.CopyFrom(request.operation)
        return AgentControlDecision(result)

    def _control_rejection(
        self,
        request_id: str,
        rejection_code: int,
        *,
        disposition: int = _DISPOSITION_REJECTED,
    ) -> AgentControlDecision:
        record = self._records.get(request_id)
        if record is not None:
            return self._decision(record.request, record.digest, disposition, rejection_code)
        occurred_at = Timestamp()
        occurred_at.FromDatetime(self.clock().astimezone(UTC))
        return AgentControlDecision(
            protocol_pb2.AgentControlActionResult(
                schema_version=ACTION_SCHEMA_VERSION,
                request_id=request_id,
                disposition=cast(protocol_pb2.AgentControlDisposition, disposition),
                rejection_code=cast(protocol_pb2.AgentControlRejectionCode, rejection_code),
                occurred_at=occurred_at,
                retryable=False,
            )
        )
