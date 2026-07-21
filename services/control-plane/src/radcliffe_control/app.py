from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, cast
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from google.protobuf.timestamp_pb2 import Timestamp
from livekit import api
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from radcliffe_domain.agent_control import (
    AgentControlBoundary,
    AgentControlGrant,
)
from radcliffe_domain.catalog import (
    InvalidPageCursorError,
    SessionCatalog,
    SessionConflictError,
    SessionRecord,
)
from radcliffe_domain.configuration import (
    SETTING_DEFINITIONS,
    ConfigurationConfirmationRequired,
    ConfigurationConflictError,
    ConfigurationContext,
    ConfigurationCorruptionError,
    ConfigurationError,
    ConfigurationKey,
    ConfigurationMutation,
    ConfigurationMutationResult,
    ConfigurationRegistry,
    ConfigurationScope,
    EffectiveConfiguration,
    configuration_key,
    normalize_configuration_value,
)
from radcliffe_domain.devices import (
    DeviceCredentialRotationError,
    DeviceEnrollmentConflictError,
    DeviceRecord,
    DeviceRegistry,
    credential_digest,
)
from radcliffe_domain.envelope import MessageEnvelope, Sensitivity
from radcliffe_domain.projection import (
    CONTEXT_PROJECTION_VERSION,
    TURN_DISPOSITION_EVENT,
    TURN_EVENT_METADATA,
    SpeechDelivery,
    context_replay,
    render_projection,
)
from radcliffe_domain.session import Operation
from radcliffe_domain.store import EventStore
from radcliffe_domain.telemetry import TelemetrySink
from radcliffe_domain.tools import CapabilityGrant, ToolDeniedError, ToolGateway
from radcliffe_protocol.radcliffe.v2 import protocol_pb2

from radcliffe_control.context_usage import (
    ContextUsageClient,
    ContextUsageClientError,
    SidecarContextUsageClient,
)
from radcliffe_control.readiness import ReadinessProbe
from radcliffe_control.settings import Settings
from radcliffe_control.workers import (
    LocalWorkerLauncher,
    WorkerLauncher,
    WorkerLaunchError,
    WorkerSupervisor,
)

logger = logging.getLogger(__name__)


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.title() for part in rest)


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,96}$")
    participant_identity: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,96}$")
    role: Literal["client"] = "client"
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=120)


class SessionCredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant_identity: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,96}$")
    role: Literal["client"] = "client"


class WorkerCredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceEnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{16,96}$")
    display_name: str = Field(min_length=1, max_length=80)
    credential: str = Field(pattern=r"^rdc_dev_[A-Za-z0-9_-]{43,128}$")


class DeviceCredentialRotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential: str = Field(pattern=r"^rdc_dev_[A-Za-z0-9_-]{43,128}$")


class DeviceResponse(BaseModel):
    device_id: str
    participant_identity: str
    display_name: str
    created_at: datetime
    last_authenticated_at: datetime | None
    revoked: bool


class DeviceAuthAuditResponse(BaseModel):
    event_id: str
    device_id: str | None
    event_type: str
    reason: str
    occurred_at: datetime


class SessionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=120)
    status: Literal["active", "ended", "archived"] = "active"


class SessionSummaryResponse(BaseModel):
    session_id: str
    created_at: datetime
    last_activity_at: datetime
    title: str | None
    status: Literal["active", "ended", "archived"]
    protocol_version: int
    generation: int
    worker_status: Literal["external", "stopped", "starting", "running", "failed"]


class SessionResponse(SessionSummaryResponse):
    livekit_url: str
    room_name: str
    participant_token: str
    participant_identity: str
    expires_in_seconds: int


class SessionListResponse(BaseModel):
    items: tuple[SessionSummaryResponse, ...]
    next_cursor: str | None


class ReadinessCheckResponse(BaseModel):
    component: str
    status: Literal["ok", "failed", "disabled"]
    code: str
    observed: int | None = None
    expected: int | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: tuple[ReadinessCheckResponse, ...]


class SidecarContextUsageResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=_to_camel,
        populate_by_name=True,
    )

    model_id: str
    context_limit: int = Field(gt=0)
    projected_input_tokens: int = Field(ge=0)
    reserved_output_tokens: int = Field(ge=0)
    remaining_tokens: int = Field(ge=0)
    percentage: float = Field(ge=0, le=100)
    projection_version: str
    measurement_basis: str
    exact: bool
    overflow: bool
    request_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class ContextUsageResponse(BaseModel):
    session_id: str
    model_id: str
    context_limit: int
    projected_input_tokens: int
    reserved_output_tokens: int
    remaining_tokens: int
    percentage: float
    projection_version: str
    measurement_basis: str
    exact: bool
    overflow: bool


class SessionMessageResponse(BaseModel):
    sequence: int
    message_id: str
    created_at: datetime
    source: Literal["user", "model", "call", "text_source", "system"]
    text: str | None
    provenance_label: str | None
    delivery_label: str | None


class SessionMessageListResponse(BaseModel):
    items: tuple[SessionMessageResponse, ...]
    next_after: int | None


class ReadSourceArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    rate: str | float | None = None

    @field_validator("rate", mode="before")
    @classmethod
    def valid_rate(cls, value: object) -> object:
        if value is not None:
            SpeechDelivery.parse(value)
        return value


class ListSourcesArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigureArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=_to_camel,
        populate_by_name=True,
    )

    operation: Literal["inspect", "set", "reset", "undo"]
    key: Literal["response_detail", "default_speech_rate"]
    scope: Literal["turn", "session"] = "session"
    value: str | None = None
    undo_mutation_id: str | None = None

    @model_validator(mode="after")
    def valid_operation_shape(self) -> ConfigureArguments:
        if self.operation == "set":
            normalize_configuration_value(self.key, self.value)
        elif self.value is not None:
            raise ValueError("only set accepts a setting value")
        if self.operation == "undo" and not self.undo_mutation_id:
            raise ValueError("undo requires undo_mutation_id")
        if self.operation != "undo" and self.undo_mutation_id is not None:
            raise ValueError("only undo accepts undo_mutation_id")
        return self


class ToolCallRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=_to_camel,
        populate_by_name=True,
    )

    session_id: str
    generation: int
    operation_id: str = Field(min_length=16, max_length=128)
    trace_id: str = Field(pattern=r"^trc_[0-9a-f]{32}$")
    cancellation_id: str | None = Field(default=None, min_length=1, max_length=128)
    turn_id: str | None = Field(default=None, min_length=1, max_length=128)
    call_id: str
    idempotency_key: str
    tool_name: Literal["list_sources", "read_source", "configure"]
    arguments: ReadSourceArguments | ListSourcesArguments | ConfigureArguments

    @model_validator(mode="after")
    def arguments_match_tool(self) -> ToolCallRequest:
        expected = {
            "list_sources": ListSourcesArguments,
            "read_source": ReadSourceArguments,
            "configure": ConfigureArguments,
        }[self.tool_name]
        if not isinstance(self.arguments, expected):
            raise ValueError(f"arguments do not match {self.tool_name}")
        if self.tool_name == "configure" and (self.cancellation_id is None or self.turn_id is None):
            raise ValueError("configure requires cancellation_id and turn_id")
        return self


class ToolCallResponse(BaseModel):
    model_content: str = Field(serialization_alias="modelContent")
    details: dict[str, str]


class ConfigurationValueResponse(BaseModel):
    key: Literal["response_detail", "default_speech_rate"]
    value: str
    effective_scope: str
    revision: int
    default: str
    allowed_values: tuple[str, ...]
    allowed_scopes: tuple[str, ...]
    persistent_scopes: tuple[str, ...]
    confirmation_by_scope: dict[str, str]


class ConfigurationListResponse(BaseModel):
    items: tuple[ConfigurationValueResponse, ...]


class ConfigurationMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["set", "reset", "undo"]
    key: Literal["response_detail", "default_speech_rate"]
    scope: Literal["device", "session", "turn"]
    value: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_revision: int | None = Field(default=None, ge=0)
    undo_mutation_id: str | None = None

    @model_validator(mode="after")
    def valid_mutation_shape(self) -> ConfigurationMutationRequest:
        if self.operation == "set":
            normalize_configuration_value(self.key, self.value)
        elif self.value is not None:
            raise ValueError("only set accepts a setting value")
        if self.scope in {"session", "turn"} and not self.session_id:
            raise ValueError(f"{self.scope} scope requires session_id")
        if self.scope == "turn" and not self.turn_id:
            raise ValueError("turn scope requires turn_id")
        if self.operation == "undo" and not self.undo_mutation_id:
            raise ValueError("undo requires undo_mutation_id")
        if self.operation != "undo" and self.undo_mutation_id is not None:
            raise ValueError("only undo accepts undo_mutation_id")
        return self


class ConfigurationMutationResponse(BaseModel):
    mutation_id: str
    previous_value: str | None
    applied_value: str | None
    revision: int
    replayed: bool
    effective: ConfigurationValueResponse


@dataclass(frozen=True, slots=True)
class ActiveSession:
    latest_operation_generation: int
    grant: CapabilityGrant


@dataclass(slots=True)
class Runtime:
    settings: Settings
    store: EventStore
    catalog: SessionCatalog
    gateway: ToolGateway
    sessions: dict[str, ActiveSession]
    lock: asyncio.Lock
    workers: WorkerSupervisor | None
    worker_maintenance: asyncio.Task[None] | None
    context_usage: ContextUsageClient
    devices: DeviceRegistry
    readiness: ReadinessProbe
    telemetry: TelemetrySink
    configuration: ConfigurationRegistry
    agent_controls: AgentControlBoundary
    configuration_sequences: dict[str, int]
    configuration_requests: dict[str, protocol_pb2.AgentControlActionRequest]


def _bearer(authorization: str | None, expected: str) -> None:
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")


def _summary(record: SessionRecord) -> SessionSummaryResponse:
    return SessionSummaryResponse(
        session_id=record.session_id,
        created_at=record.created_at,
        last_activity_at=record.last_activity_at,
        title=record.title,
        status=record.status,  # type: ignore[arg-type]
        protocol_version=record.protocol_version,
        generation=record.generation,
        worker_status=("external" if record.worker_mode == "external" else record.worker_status),  # type: ignore[arg-type]
    )


def _configuration_value_response(
    key: ConfigurationKey,
    effective: EffectiveConfiguration,
) -> ConfigurationValueResponse:
    definition = SETTING_DEFINITIONS[key]
    return ConfigurationValueResponse(
        key=key.value,
        value=effective.value,
        effective_scope=effective.effective_scope,
        revision=effective.revision,
        default=definition.default,
        allowed_values=definition.allowed_values,
        allowed_scopes=tuple(sorted(scope.value for scope in definition.allowed_scopes)),
        persistent_scopes=tuple(sorted(scope.value for scope in definition.persistent_scopes)),
        confirmation_by_scope={
            scope.value: confirmation
            for scope, confirmation in definition.confirmation_by_scope.items()
        },
    )


_CONFIGURATION_KEY_TO_PROTO = {
    ConfigurationKey.RESPONSE_DETAIL: protocol_pb2.AGENT_CONFIGURATION_KEY_RESPONSE_DETAIL,
    ConfigurationKey.DEFAULT_SPEECH_RATE: (
        protocol_pb2.AGENT_CONFIGURATION_KEY_DEFAULT_SPEECH_RATE
    ),
}
_CONFIGURATION_SCOPE_TO_PROTO = {
    ConfigurationScope.TURN: protocol_pb2.AGENT_CONTROL_SCOPE_TURN,
    ConfigurationScope.SESSION: protocol_pb2.AGENT_CONTROL_SCOPE_SESSION,
}
_RESPONSE_DETAIL_TO_PROTO = {
    "brief": protocol_pb2.RESPONSE_DETAIL_BRIEF,
    "standard": protocol_pb2.RESPONSE_DETAIL_STANDARD,
    "detailed": protocol_pb2.RESPONSE_DETAIL_DETAILED,
}
_SPEECH_RATE_TO_PROTO = {
    "0.75x": protocol_pb2.SPEECH_RATE_SLOW_0_75X,
    "1.0x": protocol_pb2.SPEECH_RATE_NORMAL_1_0X,
    "1.25x": protocol_pb2.SPEECH_RATE_FAST_1_25X,
    "1.5x": protocol_pb2.SPEECH_RATE_VERY_FAST_1_5X,
}


def _configuration_action_request(
    request: ToolCallRequest,
    arguments: ConfigureArguments,
    sequence: int,
    requested_at: Timestamp | None = None,
) -> protocol_pb2.AgentControlActionRequest:
    key = configuration_key(arguments.key)
    scope = ConfigurationScope(arguments.scope)
    timestamp = Timestamp()
    if requested_at is None:
        timestamp.GetCurrentTime()
    else:
        timestamp.CopyFrom(requested_at)
    action = protocol_pb2.AgentControlActionRequest(
        schema_version=1,
        request_id=f"configuration:{request.call_id}",
        operation=protocol_pb2.OperationRef(
            session_id=request.session_id,
            generation=request.generation,
            operation_id=request.operation_id,
            cancellation_id=request.cancellation_id,
            trace_id=request.trace_id,
        ),
        origin=protocol_pb2.AgentControlOrigin(
            source=protocol_pb2.SOURCE_KIND_CALL,
            actor_id="agent-sidecar",
            source_message_id=f"call:{request.call_id}",
            tool_call_id=request.call_id,
        ),
        target=protocol_pb2.AgentControlTarget(
            scope=_CONFIGURATION_SCOPE_TO_PROTO[scope],
            session_id=request.session_id,
            turn_id=request.turn_id if scope is ConfigurationScope.TURN else "",
        ),
        request_sequence=sequence,
        idempotency_key=request.idempotency_key,
        confirmation=protocol_pb2.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_NONE,
        requested_at=timestamp,
    )
    if arguments.operation == "set":
        action.action_id = protocol_pb2.AGENT_CONTROL_ACTION_ID_CONFIGURATION_SET
        action.configuration.key = _CONFIGURATION_KEY_TO_PROTO[key]
        value = normalize_configuration_value(key, arguments.value)
        if key is ConfigurationKey.RESPONSE_DETAIL:
            action.configuration.response_detail = _RESPONSE_DETAIL_TO_PROTO[value]
        else:
            action.configuration.speech_rate = _SPEECH_RATE_TO_PROTO[value]
    elif arguments.operation == "inspect":
        action.action_id = protocol_pb2.AGENT_CONTROL_ACTION_ID_CONFIGURATION_INSPECT
        action.configuration_inspect.key = _CONFIGURATION_KEY_TO_PROTO[key]
    elif arguments.operation == "reset":
        action.action_id = protocol_pb2.AGENT_CONTROL_ACTION_ID_CONFIGURATION_RESET
        action.configuration_reset.key = _CONFIGURATION_KEY_TO_PROTO[key]
    else:
        action.action_id = protocol_pb2.AGENT_CONTROL_ACTION_ID_CONFIGURATION_UNDO
        action.configuration_undo.key = _CONFIGURATION_KEY_TO_PROTO[key]
        action.configuration_undo.mutation_id = arguments.undo_mutation_id or ""
    return action


async def _execute_configuration_tool(
    active: Runtime,
    request: ToolCallRequest,
    arguments: ConfigureArguments,
) -> ToolCallResponse:
    previous_request = active.configuration_requests.get(request.idempotency_key)
    if previous_request is None:
        sequence = active.configuration_sequences.get(request.session_id, 0) + 1
        active.configuration_sequences[request.session_id] = sequence
        requested_at = None
    else:
        sequence = previous_request.request_sequence
        requested_at = previous_request.requested_at
    action = _configuration_action_request(request, arguments, sequence, requested_at)
    grant = AgentControlGrant(
        grant_id=f"configuration:{request.session_id}:{request.generation}",
        session_id=request.session_id,
        generation=request.generation,
        actor_id="agent-sidecar",
        allowed_origins=frozenset({protocol_pb2.SOURCE_KIND_CALL}),
        allowed_actions=frozenset(
            {
                protocol_pb2.AGENT_CONTROL_ACTION_ID_CONFIGURATION_SET,
                protocol_pb2.AGENT_CONTROL_ACTION_ID_CONFIGURATION_INSPECT,
                protocol_pb2.AGENT_CONTROL_ACTION_ID_CONFIGURATION_RESET,
                protocol_pb2.AGENT_CONTROL_ACTION_ID_CONFIGURATION_UNDO,
            }
        ),
        allowed_scopes=frozenset(
            {
                protocol_pb2.AGENT_CONTROL_SCOPE_TURN,
                protocol_pb2.AGENT_CONTROL_SCOPE_SESSION,
            }
        ),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    decision = active.agent_controls.submit(
        action,
        grant=grant,
        current_generation=request.generation,
    )
    if decision.result.disposition != protocol_pb2.AGENT_CONTROL_DISPOSITION_AUTHORIZED:
        status_code = (
            status.HTTP_409_CONFLICT
            if decision.result.rejection_code
            in {
                protocol_pb2.AGENT_CONTROL_REJECTION_CODE_IDEMPOTENCY_CONFLICT,
                protocol_pb2.AGENT_CONTROL_REJECTION_CODE_CONFLICT,
            }
            else status.HTTP_403_FORBIDDEN
        )
        raise HTTPException(
            status_code=status_code,
            detail=f"configuration action rejected ({decision.result.rejection_code})",
        )
    if previous_request is None:
        active.configuration_requests[request.idempotency_key] = action

    key = configuration_key(arguments.key)
    scope = ConfigurationScope(arguments.scope)
    context = ConfigurationContext(
        owner_id=active.settings.owner_id,
        session_id=request.session_id,
        turn_id=request.turn_id,
    )
    mutation_result: ConfigurationMutationResult | None = None
    try:
        if arguments.operation == "inspect":
            effective = await active.configuration.inspect(key, context)
        else:
            mutation_result = await active.configuration.mutate(
                ConfigurationMutation(
                    mutation_id=f"configuration:{request.call_id}",
                    idempotency_key=request.idempotency_key,
                    key=key,
                    scope=scope,
                    kind=arguments.operation,
                    context=context,
                    actor_id="agent-sidecar",
                    value=arguments.value,
                    undo_mutation_id=arguments.undo_mutation_id,
                )
            )
            effective = mutation_result.effective
    except ConfigurationConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ConfigurationConfirmationRequired as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ConfigurationCorruptionError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ConfigurationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    payload = {
        "key": key.value,
        "value": effective.value,
        "effective_scope": effective.effective_scope,
        "revision": effective.revision,
        "operation": arguments.operation,
        "mutation_id": mutation_result.mutation_id if mutation_result else None,
        "replayed": mutation_result.replayed if mutation_result else decision.replayed,
    }
    return ToolCallResponse(
        model_content=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        details={
            "key": key.value,
            "value": effective.value,
            "effectiveScope": effective.effective_scope,
            "revision": str(effective.revision),
            "operation": arguments.operation,
            "mutationId": mutation_result.mutation_id if mutation_result else "",
            "replayed": str(payload["replayed"]).lower(),
        },
    )


def _room_token(
    settings: Settings,
    record: SessionRecord,
    *,
    participant_identity: str,
    role: Literal["client", "worker"],
) -> str:
    sources = ["microphone"] if role == "client" else None
    return (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(participant_identity)
        .with_ttl(timedelta(seconds=settings.room_token_ttl_seconds))
        .with_attributes(
            {
                "radcliffe.protocol": str(record.protocol_version),
                "radcliffe.generation": str(record.generation),
                "radcliffe.token_nonce": uuid4().hex,
            }
        )
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=record.session_id,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
                can_publish_sources=sources,
            )
        )
        .to_jwt()
    )


def _worker_identity(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    return f"radcliffe-worker-{digest}"


def create_app(
    provided_settings: Settings | None = None,
    worker_launcher: WorkerLauncher | None = None,
    context_usage_client: ContextUsageClient | None = None,
    readiness_probe: ReadinessProbe | None = None,
) -> FastAPI:
    runtime: Runtime | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        nonlocal runtime
        settings = provided_settings or Settings()
        store = EventStore(settings.database_path)
        await store.migrate()
        catalog = SessionCatalog(settings.database_path)
        workers: WorkerSupervisor | None = None
        if settings.managed_workers_enabled:
            launcher = worker_launcher or LocalWorkerLauncher(
                command=(
                    str(settings.worker_uv_bin),
                    "run",
                    "python",
                    "-m",
                    "radcliffe_media.production",
                ),
                workspace_root=settings.worker_workspace_root,
                log_directory=settings.worker_log_directory,
                database_path=settings.database_path,
                livekit_url=settings.livekit_url,
                token_factory=lambda record: _room_token(
                    settings,
                    record,
                    participant_identity=_worker_identity(record.session_id),
                    role="worker",
                ),
                owner_id=settings.owner_id,
            )
            workers = WorkerSupervisor(
                catalog=catalog,
                principal_id=settings.owner_id,
                launcher=launcher,
                stop_timeout_seconds=settings.worker_stop_timeout_seconds,
            )
        runtime = Runtime(
            settings,
            store,
            catalog,
            ToolGateway(store),
            {},
            asyncio.Lock(),
            workers,
            None,
            context_usage_client or SidecarContextUsageClient(settings.sidecar_url),
            DeviceRegistry(settings.database_path),
            readiness_probe or ReadinessProbe(settings),
            TelemetrySink(settings.telemetry_path),
            ConfigurationRegistry(settings.database_path),
            AgentControlBoundary(),
            {},
            {},
        )
        await reconcile_workers(runtime)
        if workers is not None:
            runtime.worker_maintenance = asyncio.create_task(
                maintain_workers(runtime),
                name="radcliffe-worker-maintenance",
            )
        try:
            yield
        finally:
            if runtime.worker_maintenance is not None:
                runtime.worker_maintenance.cancel()
                with suppress(asyncio.CancelledError):
                    await runtime.worker_maintenance
            if runtime.workers is not None:
                await runtime.workers.close()
            runtime = None

    app = FastAPI(title="Radcliffe Control Plane", version="0.1.0", lifespan=lifespan)

    def get_runtime() -> Runtime:
        if runtime is None:
            raise HTTPException(status_code=503, detail="control plane is starting")
        return runtime

    async def require_device(
        active: Runtime = Depends(get_runtime),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
    ) -> DeviceRecord:
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if (
            not authorization
            or not authorization.startswith("Bearer ")
            or not 51 <= len(supplied) <= 136
            or not supplied.startswith("rdc_dev_")
        ):
            await active.devices.audit(
                owner_id=active.settings.owner_id,
                device_id=None,
                event_type="authentication_failed",
                reason="malformed_credential",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid device credential",
            )
        device = await active.devices.authenticate(credential_digest(supplied))
        if device is None or device.owner_id != active.settings.owner_id:
            await active.devices.audit(
                owner_id=active.settings.owner_id,
                device_id=None,
                event_type="authentication_failed",
                reason="unknown_credential",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid device credential",
            )
        if device.revoked_at is not None:
            await active.devices.audit(
                owner_id=active.settings.owner_id,
                device_id=device.device_id,
                event_type="authentication_failed",
                reason="revoked_credential",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid device credential",
            )
        return device

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok" if runtime is not None else "starting"}

    @app.get("/readyz", response_model=ReadinessResponse)
    async def readiness(
        response: Response,
        active: Runtime = Depends(get_runtime),  # noqa: B008
    ) -> ReadinessResponse:
        maintenance = active.worker_maintenance
        report = await active.readiness.check(
            worker_supervisor_available=active.workers is not None,
            worker_maintenance_running=maintenance is not None and not maintenance.done(),
        )
        active.telemetry.emit(
            component="control",
            event="readiness",
            outcome="ready" if report.status == "ready" else "failed",
            session_id="control-plane",
            generation=0,
            phase="dependencies",
        )
        if report.status != "ready":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status=report.status,
            checks=tuple(
                ReadinessCheckResponse(
                    component=check.component,
                    status=check.status,
                    code=check.code,
                    observed=check.observed,
                    expected=check.expected,
                )
                for check in report.checks
            ),
        )

    async def reconcile_workers(active: Runtime) -> None:
        if active.workers is None:
            return
        active_since = datetime.now(UTC) - timedelta(seconds=active.settings.worker_idle_seconds)
        records = await active.catalog.managed_active_since(
            principal_id=active.settings.owner_id,
            active_since=active_since,
        )
        await active.workers.reconcile(records)

    async def maintain_workers(active: Runtime) -> None:
        while True:
            await asyncio.sleep(active.settings.worker_reconcile_seconds)
            try:
                await reconcile_workers(active)
            except Exception as error:
                logger.error(
                    "managed worker reconciliation failed type=%s",
                    type(error).__name__,
                )

    async def ensure_worker(active: Runtime, record: SessionRecord) -> SessionRecord:
        if active.workers is None or record.worker_mode != "managed":
            return record
        try:
            return await active.workers.ensure(record)
        except WorkerLaunchError as exc:
            raise HTTPException(status_code=503, detail="media worker failed to start") from exc

    async def issue_credentials(
        *,
        active: Runtime,
        record: SessionRecord,
        participant_identity: str,
        role: Literal["client", "worker"],
    ) -> SessionResponse:
        refreshed = await active.catalog.touch(
            principal_id=active.settings.owner_id,
            session_id=record.session_id,
        )
        assert refreshed is not None
        grant = CapabilityGrant(
            grant_id=f"source:{record.session_id}:{record.generation}",
            session_id=record.session_id,
            generation=record.generation,
            readable_roots=(active.settings.source_root.resolve(),),
            allowed_tools=frozenset({"list_sources", "read_source"}),
            expires_at=datetime.now(UTC)
            + timedelta(seconds=active.settings.room_token_ttl_seconds),
        )
        async with active.lock:
            active.sessions[record.session_id] = ActiveSession(record.generation, grant)

        token = _room_token(
            active.settings,
            record,
            participant_identity=participant_identity,
            role=role,
        )
        return SessionResponse(
            **_summary(refreshed).model_dump(),
            livekit_url=active.settings.livekit_url,
            room_name=record.session_id,
            participant_token=token,
            participant_identity=participant_identity,
            expires_in_seconds=active.settings.room_token_ttl_seconds,
        )

    @app.post("/v1/devices/enroll", response_model=DeviceResponse)
    async def enroll_device(
        request: DeviceEnrollmentRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
    ) -> DeviceResponse:
        _bearer(authorization, active.settings.bootstrap_token)
        try:
            device, created = await active.devices.enroll(
                owner_id=active.settings.owner_id,
                installation_id=request.installation_id,
                display_name=request.display_name,
                credential_sha256=credential_digest(request.credential),
            )
        except DeviceEnrollmentConflictError as error:
            await active.devices.audit(
                owner_id=active.settings.owner_id,
                device_id=None,
                event_type="enrollment_conflict",
                reason="installation_or_credential_replay",
            )
            raise HTTPException(status_code=409, detail=str(error)) from error
        if created:
            await active.devices.audit(
                owner_id=active.settings.owner_id,
                device_id=device.device_id,
                event_type="enrolled",
                reason="new_installation",
            )
        return DeviceResponse(
            device_id=device.device_id,
            participant_identity=device.participant_identity,
            display_name=device.display_name,
            created_at=device.created_at,
            last_authenticated_at=device.last_authenticated_at,
            revoked=device.revoked_at is not None,
        )

    @app.get("/v1/devices/me", response_model=DeviceResponse)
    async def get_current_device(
        device: DeviceRecord = Depends(require_device),  # noqa: B008
    ) -> DeviceResponse:
        return DeviceResponse(
            device_id=device.device_id,
            participant_identity=device.participant_identity,
            display_name=device.display_name,
            created_at=device.created_at,
            last_authenticated_at=device.last_authenticated_at,
            revoked=False,
        )

    @app.post("/v1/devices/me/credential", response_model=DeviceResponse)
    async def rotate_current_device_credential(
        request: DeviceCredentialRotationRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        device: DeviceRecord = Depends(require_device),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
    ) -> DeviceResponse:
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        try:
            rotated, changed = await active.devices.rotate_credential(
                owner_id=active.settings.owner_id,
                device_id=device.device_id,
                current_credential_sha256=credential_digest(supplied),
                next_credential_sha256=credential_digest(request.credential),
            )
        except DeviceCredentialRotationError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if changed:
            await active.devices.audit(
                owner_id=active.settings.owner_id,
                device_id=rotated.device_id,
                event_type="rotated",
                reason="device_self_rotation",
            )
        return DeviceResponse(
            device_id=rotated.device_id,
            participant_identity=rotated.participant_identity,
            display_name=rotated.display_name,
            created_at=rotated.created_at,
            last_authenticated_at=rotated.last_authenticated_at,
            revoked=False,
        )

    @app.post("/v1/devices/{device_id}/revoke", response_model=DeviceResponse)
    async def revoke_device(
        device_id: str,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
    ) -> DeviceResponse:
        _bearer(authorization, active.settings.internal_token)
        existing = await active.devices.get(device_id)
        if existing is None or existing.owner_id != active.settings.owner_id:
            raise HTTPException(status_code=404, detail="device not found")
        device = await active.devices.revoke(
            owner_id=active.settings.owner_id,
            device_id=device_id,
        )
        assert device is not None
        if existing.revoked_at is None:
            await active.devices.audit(
                owner_id=active.settings.owner_id,
                device_id=device.device_id,
                event_type="revoked",
                reason="operator_revocation",
            )
        return DeviceResponse(
            device_id=device.device_id,
            participant_identity=device.participant_identity,
            display_name=device.display_name,
            created_at=device.created_at,
            last_authenticated_at=device.last_authenticated_at,
            revoked=True,
        )

    @app.get(
        "/v1/internal/device-auth-audit",
        response_model=tuple[DeviceAuthAuditResponse, ...],
    )
    async def get_device_auth_audit(
        active: Runtime = Depends(get_runtime),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
    ) -> tuple[DeviceAuthAuditResponse, ...]:
        _bearer(authorization, active.settings.internal_token)
        events = await active.devices.audit_events(owner_id=active.settings.owner_id)
        return tuple(
            DeviceAuthAuditResponse(
                event_id=event.event_id,
                device_id=event.device_id,
                event_type=event.event_type,
                reason=event.reason,
                occurred_at=event.occurred_at,
            )
            for event in events
        )

    @app.post("/v1/sessions", response_model=SessionResponse)
    async def create_session(
        request: SessionRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        device: DeviceRecord = Depends(require_device),  # noqa: B008
    ) -> SessionResponse:
        try:
            record, _created = await active.catalog.create(
                principal_id=active.settings.owner_id,
                session_id=request.session_id,
                idempotency_key=request.idempotency_key,
                title=request.title,
            )
        except SessionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        record = await ensure_worker(active, record)
        return await issue_credentials(
            active=active,
            record=record,
            participant_identity=device.participant_identity,
            role="client",
        )

    @app.get("/v1/sessions", response_model=SessionListResponse)
    async def list_sessions(
        active: Runtime = Depends(get_runtime),  # noqa: B008
        _device: DeviceRecord = Depends(require_device),  # noqa: B008
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: str | None = None,
        include_archived: bool = False,
        status: Literal["active", "ended", "archived"] | None = None,
    ) -> SessionListResponse:
        try:
            page = await active.catalog.list(
                principal_id=active.settings.owner_id,
                limit=limit,
                cursor=cursor,
                include_archived=include_archived,
                status=status,
            )
        except InvalidPageCursorError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return SessionListResponse(
            items=tuple(_summary(item) for item in page.items),
            next_cursor=page.next_cursor,
        )

    @app.get("/v1/sessions/{session_id}", response_model=SessionSummaryResponse)
    async def get_session(
        session_id: str,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        _device: DeviceRecord = Depends(require_device),  # noqa: B008
    ) -> SessionSummaryResponse:
        record = await active.catalog.get(
            principal_id=active.settings.owner_id,
            session_id=session_id,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="session not found")
        return _summary(record)

    @app.get(
        "/v1/sessions/{session_id}/context-usage",
        response_model=ContextUsageResponse,
    )
    async def get_context_usage(
        session_id: str,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        _device: DeviceRecord = Depends(require_device),  # noqa: B008
    ) -> ContextUsageResponse:
        record = await active.catalog.get(
            principal_id=active.settings.owner_id,
            session_id=session_id,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="session not found")
        history = await active.store.replay(session_id)
        packets = context_replay(history)
        request: dict[str, object] = {
            "requestId": f"context-usage:{session_id}:{len(history)}",
            "sessionId": session_id,
            "generation": record.generation,
            "packets": [
                packet.to_wire(int(envelope.created_at.timestamp() * 1000))
                for envelope, packet in zip(history, packets, strict=True)
            ],
        }
        try:
            measured = SidecarContextUsageResponse.model_validate(
                await active.context_usage.measure(request)
            )
        except (ContextUsageClientError, ValidationError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if measured.projection_version != CONTEXT_PROJECTION_VERSION:
            raise HTTPException(status_code=503, detail="context projection version mismatch")
        return ContextUsageResponse(
            session_id=session_id,
            **measured.model_dump(exclude={"request_digest"}),
        )

    @app.get(
        "/v1/sessions/{session_id}/messages",
        response_model=SessionMessageListResponse,
    )
    async def get_session_messages(
        session_id: str,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        _device: DeviceRecord = Depends(require_device),  # noqa: B008
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> SessionMessageListResponse:
        record = await active.catalog.get(
            principal_id=active.settings.owner_id,
            session_id=session_id,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="session not found")
        visible: list[MessageEnvelope] = []
        cursor = after
        exhausted = False
        while len(visible) <= limit and not exhausted:
            page = await active.store.replay_page(session_id, after=cursor, limit=100)
            if not page.items:
                break
            for envelope in page.items:
                cursor = envelope.sequence
                if (
                    envelope.sensitivity is not Sensitivity.SECRET
                    and envelope.metadata.get(TURN_EVENT_METADATA) != TURN_DISPOSITION_EVENT
                ):
                    visible.append(envelope)
                if len(visible) > limit:
                    break
            exhausted = page.next_after is None
        items = visible[:limit]
        return SessionMessageListResponse(
            items=tuple(
                SessionMessageResponse(
                    sequence=envelope.sequence,
                    message_id=envelope.message_id,
                    created_at=envelope.created_at,
                    source=envelope.source.value,
                    text=envelope.content.inline_text,
                    provenance_label=(
                        envelope.provenance.display_name or envelope.provenance.locator or None
                    ),
                    delivery_label=render_projection(envelope).delivery_summary,
                )
                for envelope in items
            ),
            next_after=items[-1].sequence if len(visible) > limit else None,
        )

    @app.patch("/v1/sessions/{session_id}", response_model=SessionSummaryResponse)
    async def update_session(
        session_id: str,
        request: SessionUpdateRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        _device: DeviceRecord = Depends(require_device),  # noqa: B008
    ) -> SessionSummaryResponse:
        existing = await active.catalog.get(
            principal_id=active.settings.owner_id,
            session_id=session_id,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="session not found")
        record = await active.catalog.update(
            principal_id=active.settings.owner_id,
            session_id=session_id,
            title=request.title if "title" in request.model_fields_set else existing.title,
            status=request.status,
        )
        assert record is not None
        if active.workers is not None and record.worker_mode == "managed":
            if record.status == "active":
                record = await ensure_worker(active, record)
            else:
                await active.workers.stop(record.session_id)
                refreshed = await active.catalog.get(
                    principal_id=active.settings.owner_id,
                    session_id=record.session_id,
                )
                assert refreshed is not None
                record = refreshed
        return _summary(record)

    @app.post(
        "/v1/sessions/{session_id}/credentials",
        response_model=SessionResponse,
    )
    async def reopen_session(
        session_id: str,
        request: SessionCredentialsRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        device: DeviceRecord = Depends(require_device),  # noqa: B008
    ) -> SessionResponse:
        record = await active.catalog.get(
            principal_id=active.settings.owner_id,
            session_id=session_id,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="session not found")
        record = await ensure_worker(active, record)
        return await issue_credentials(
            active=active,
            record=record,
            participant_identity=device.participant_identity,
            role="client",
        )

    @app.post(
        "/v1/internal/sessions/{session_id}/worker-credentials",
        response_model=SessionResponse,
    )
    async def issue_worker_credentials(
        session_id: str,
        _request: WorkerCredentialsRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
    ) -> SessionResponse:
        _bearer(authorization, active.settings.internal_token)
        record = await active.catalog.get(
            principal_id=active.settings.owner_id,
            session_id=session_id,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="session not found")
        return await issue_credentials(
            active=active,
            record=record,
            participant_identity=_worker_identity(record.session_id),
            role="worker",
        )

    @app.get("/v1/configuration", response_model=ConfigurationListResponse)
    async def get_configuration(
        active: Runtime = Depends(get_runtime),  # noqa: B008
        device: DeviceRecord = Depends(require_device),  # noqa: B008
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> ConfigurationListResponse:
        if session_id is not None:
            record = await active.catalog.get(
                principal_id=active.settings.owner_id,
                session_id=session_id,
            )
            if record is None:
                raise HTTPException(status_code=404, detail="session not found")
        context = ConfigurationContext(
            owner_id=active.settings.owner_id,
            device_id=device.device_id,
            session_id=session_id,
            turn_id=turn_id,
        )
        try:
            values: list[ConfigurationValueResponse] = []
            for key in ConfigurationKey:
                values.append(
                    _configuration_value_response(
                        key,
                        await active.configuration.inspect(key, context),
                    )
                )
        except ConfigurationCorruptionError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return ConfigurationListResponse(items=tuple(values))

    @app.post(
        "/v1/configuration/mutations",
        response_model=ConfigurationMutationResponse,
    )
    async def mutate_configuration(
        request: ConfigurationMutationRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        device: DeviceRecord = Depends(require_device),  # noqa: B008
    ) -> ConfigurationMutationResponse:
        if request.session_id is not None:
            record = await active.catalog.get(
                principal_id=active.settings.owner_id,
                session_id=request.session_id,
            )
            if record is None:
                raise HTTPException(status_code=404, detail="session not found")
        context = ConfigurationContext(
            owner_id=active.settings.owner_id,
            device_id=device.device_id,
            session_id=request.session_id,
            turn_id=request.turn_id,
        )
        mutation_id = (
            "configuration:"
            + hashlib.sha256(f"{device.device_id}:{request.idempotency_key}".encode()).hexdigest()[
                :32
            ]
        )
        try:
            result = await active.configuration.mutate(
                ConfigurationMutation(
                    mutation_id=mutation_id,
                    idempotency_key=request.idempotency_key,
                    key=request.key,
                    scope=request.scope,
                    kind=request.operation,
                    context=context,
                    actor_id=device.device_id,
                    value=request.value,
                    expected_revision=request.expected_revision,
                    undo_mutation_id=request.undo_mutation_id,
                    confirmed=True,
                )
            )
        except ConfigurationConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ConfigurationConfirmationRequired as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ConfigurationCorruptionError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except ConfigurationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return ConfigurationMutationResponse(
            mutation_id=result.mutation_id,
            previous_value=result.previous_value,
            applied_value=result.applied_value,
            revision=result.revision,
            replayed=result.replayed,
            effective=_configuration_value_response(result.key, result.effective),
        )

    @app.post("/v1/tool-calls", response_model=ToolCallResponse)
    async def execute_tool(
        request: ToolCallRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
    ) -> ToolCallResponse:
        _bearer(authorization, active.settings.internal_token)
        operation = Operation(
            session_id=request.session_id,
            generation=request.generation,
            operation_id=request.operation_id,
            cancellation_id=request.cancellation_id or f"tool:{request.call_id}",
            trace_id=request.trace_id,
        )
        active.telemetry.emit(
            component="control",
            event="tool",
            outcome="started",
            session_id=request.session_id,
            generation=request.generation,
            phase="authorization",
            operation=operation,
        )
        async with active.lock:
            session = active.sessions.get(request.session_id)
            if session is None:
                active.telemetry.emit(
                    component="control",
                    event="tool_error",
                    outcome="failed",
                    session_id=request.session_id,
                    generation=request.generation,
                    phase="authorization",
                    operation=operation,
                )
                raise HTTPException(status_code=403, detail="unknown session")
            if request.generation < session.latest_operation_generation:
                active.telemetry.emit(
                    component="control",
                    event="tool_error",
                    outcome="failed",
                    session_id=request.session_id,
                    generation=request.generation,
                    phase="authorization",
                    operation=operation,
                )
                raise HTTPException(status_code=403, detail="stale operation generation")
            grant = session.grant
            if request.generation > session.latest_operation_generation:
                grant = replace(
                    grant,
                    grant_id=f"source:{request.session_id}:operation-{request.generation}",
                    generation=request.generation,
                )
                active.sessions[request.session_id] = ActiveSession(
                    request.generation,
                    grant,
                )
            try:
                if request.tool_name == "configure":
                    response = await _execute_configuration_tool(
                        active,
                        request,
                        cast(ConfigureArguments, request.arguments),
                    )
                    active.telemetry.emit(
                        component="control",
                        event="tool",
                        outcome="completed",
                        session_id=request.session_id,
                        generation=request.generation,
                        phase="execution",
                        operation=operation,
                    )
                    return response
                if request.tool_name == "list_sources":
                    catalog = await active.gateway.list_sources(
                        call_id=request.call_id,
                        idempotency_key=request.idempotency_key,
                        session_id=request.session_id,
                        generation=request.generation,
                        grant=grant,
                        raw_arguments=request.arguments.model_dump(),
                    )
                    model_content = json.dumps(
                        {"paths": catalog.paths, "truncated": catalog.truncated},
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    active.telemetry.emit(
                        component="control",
                        event="tool",
                        outcome="completed",
                        session_id=request.session_id,
                        generation=request.generation,
                        phase="execution",
                        operation=operation,
                    )
                    return ToolCallResponse(
                        model_content=model_content,
                        details={
                            "catalogCount": str(len(catalog.paths)),
                            "truncated": str(catalog.truncated).lower(),
                        },
                    )
                result = await active.gateway.read_source(
                    call_id=request.call_id,
                    idempotency_key=request.idempotency_key,
                    session_id=request.session_id,
                    generation=request.generation,
                    grant=grant,
                    raw_arguments=request.arguments.model_dump(exclude_none=True),
                )
            except ToolDeniedError as exc:
                active.telemetry.emit(
                    component="control",
                    event="tool_error",
                    outcome="failed",
                    session_id=request.session_id,
                    generation=request.generation,
                    phase="authorization",
                    operation=operation,
                )
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except Exception:
                active.telemetry.emit(
                    component="control",
                    event="tool_error",
                    outcome="failed",
                    session_id=request.session_id,
                    generation=request.generation,
                    phase="execution",
                    operation=operation,
                )
                raise
        source = result.source_envelope
        source_wire = source.to_proto().SerializeToString(deterministic=True)
        active.telemetry.emit(
            component="control",
            event="tool",
            outcome="completed",
            session_id=request.session_id,
            generation=request.generation,
            phase="execution",
            operation=operation,
        )
        return ToolCallResponse(
            model_content=(
                f"Canonical source {source.message_id} was authorized and handed directly "
                "to Radcliffe rendering. Do not reproduce its text."
            ),
            details={
                "sourceMessageId": source.message_id,
                "contentSha256": source.content_sha256.hex(),
                "locator": source.provenance.locator,
                "sourceEnvelopeProtoBase64": base64.b64encode(source_wire).decode("ascii"),
            },
        )

    return app


app = create_app()
