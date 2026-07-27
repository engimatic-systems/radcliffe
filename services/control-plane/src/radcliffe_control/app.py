from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, cast
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from google.protobuf.message import DecodeError
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
from radcliffe_domain.envelope import MessageEnvelope, Provenance, Sensitivity, SourceKind
from radcliffe_domain.jobs import (
    JobConflictError,
    JobError,
    JobEventKind,
    JobEventRecord,
    JobInput,
    JobLeaseError,
    JobMutationResult,
    JobNotFoundError,
    JobNotificationPolicy,
    JobRecord,
    JobRelatedLink,
    JobStateError,
    JobStatus,
    JobStore,
)
from radcliffe_domain.projection import (
    BACKGROUND_JOB_OUTCOME_EVENT,
    BACKGROUND_JOB_REFERENCE_EVENT,
    CONTEXT_PROJECTION_VERSION,
    TURN_DISPOSITION_EVENT,
    TURN_EVENT_METADATA,
    SpeechDelivery,
    context_replay,
    render_projection,
)
from radcliffe_domain.session import Operation
from radcliffe_domain.session_actions import (
    SessionActionConflictError,
    SessionActionError,
    SessionActionKind,
    SessionResolutionStatus,
    SessionTransitionResult,
    SessionTransitionStore,
    resolve_session_reference,
)
from radcliffe_domain.store import EventStore
from radcliffe_domain.telemetry import TelemetrySink
from radcliffe_domain.tools import (
    MAX_SOURCE_BOUNDARY,
    CapabilityGrant,
    ToolDeniedError,
    ToolGateway,
)
from radcliffe_protocol.radcliffe.v2 import protocol_pb2

from radcliffe_control.context_usage import (
    ContextUsageClient,
    ContextUsageClientError,
    SidecarContextUsageClient,
)
from radcliffe_control.job_executor import (
    JobExecutorConfigurationError,
    JobExecutorSupervisor,
    JobLauncher,
    JsonLineJobLauncher,
)
from radcliffe_control.job_results import (
    PromotedArtifactFile,
    PromotedJobResult,
    PromotedSource,
    PromotedSourceFile,
    read_promoted_artifacts,
    read_promoted_result,
    read_promoted_sources,
)
from radcliffe_control.readiness import ReadinessProbe
from radcliffe_control.resume_cost import ModelPricingRegistry, ResumeCostEstimate
from radcliffe_control.settings import Settings
from radcliffe_control.workers import (
    LocalWorkerLauncher,
    WorkerLauncher,
    WorkerLaunchError,
    WorkerSupervisor,
)

logger = logging.getLogger(__name__)
_SPOKEN_OUTPUT_CATEGORIES = frozenset(
    {
        "hash",
        "identifier",
        "url",
        "path",
        "timestamp",
        "stack_trace",
        "serialized_data",
        "long_number",
        "code",
        "citation",
        "repeated_punctuation",
        "acronym_metadata",
    }
)


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.title() for part in rest)


def _technical_detail_categories(envelope: MessageEnvelope) -> tuple[str, ...]:
    if envelope.source is not SourceKind.MODEL:
        return ()
    return tuple(
        sorted(
            set(envelope.metadata.get("radcliffe.spoken_output.categories", "").split(","))
            & _SPOKEN_OUTPUT_CATEGORIES
        )
    )


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


class HealthResponse(BaseModel):
    status: Literal["ok", "starting"]
    build_revision: str
    capabilities: tuple[str, ...]
    background_executor_backend: Literal["custom-loop", "claude-agent-sdk"]
    background_model_provider: Literal["stub", "anthropic", "ollama"]
    background_model_id: str


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
    resume_cost: ResumeCostEstimate


class SessionMessageResponse(BaseModel):
    sequence: int
    message_id: str
    created_at: datetime
    source: Literal["user", "model", "call", "text_source", "system"]
    text: str | None
    provenance_label: str | None
    delivery_label: str | None
    technical_detail_categories: tuple[str, ...]


class SessionMessageListResponse(BaseModel):
    items: tuple[SessionMessageResponse, ...]
    next_after: int | None


class ReadSourceArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=_to_camel,
        populate_by_name=True,
    )

    path: str
    rate: str | float | None = None
    start_at_boundary: int | None = Field(default=None, ge=0, le=MAX_SOURCE_BOUNDARY)

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


class SessionActionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["create", "switch", "restart", "end"]
    reference: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def valid_operation_shape(self) -> SessionActionArguments:
        if self.operation == "switch" and self.reference is None:
            raise ValueError("switch requires a recent-session reference")
        if self.operation != "switch" and self.reference is not None:
            raise ValueError("only switch accepts a recent-session reference")
        return self


class RunJobInputArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=_to_camel,
        populate_by_name=True,
    )

    kind: Literal["source", "artifact"]
    reference: str = Field(min_length=1, max_length=2_048)
    job_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
    )

    @model_validator(mode="after")
    def valid_kind_shape(self) -> RunJobInputArguments:
        if self.kind == "source" and self.job_id is not None:
            raise ValueError("foreground source inputs do not accept a job ID")
        return self


class RunJobArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=_to_camel,
        populate_by_name=True,
    )

    instruction: str = Field(min_length=1, max_length=12_000)
    notification_policy: Literal["immediate", "next_safe_pause", "silent", "user_waiting"] = (
        "next_safe_pause"
    )
    priority: int = Field(default=2, ge=0, le=4)
    budget_usd: float | None = Field(default=None, gt=0, le=100)
    inputs: tuple[RunJobInputArguments, ...] = Field(default=(), max_length=8)


class ManageJobArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=_to_camel,
        populate_by_name=True,
    )

    operation: Literal["status", "follow_up", "set_priority", "cancel", "retry"]
    job_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
    instruction: str | None = Field(default=None, min_length=1, max_length=4_000)
    priority: int | None = Field(default=None, ge=0, le=4)

    @model_validator(mode="after")
    def valid_operation_shape(self) -> ManageJobArguments:
        if (self.operation == "follow_up") != (self.instruction is not None):
            raise ValueError("only follow_up requires an instruction")
        if (self.operation == "set_priority") != (self.priority is not None):
            raise ValueError("only set_priority requires a priority")
        return self


class ReadJobSourceArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=_to_camel,
        populate_by_name=True,
    )

    reference: str = Field(min_length=1, max_length=300)
    job_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
    )
    rate: str | float | None = None
    start_at_boundary: int | None = Field(default=None, ge=0, le=MAX_SOURCE_BOUNDARY)

    @field_validator("rate", mode="before")
    @classmethod
    def valid_rate(cls, value: object) -> object:
        if value is not None:
            SpeechDelivery.parse(value)
        return value


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
    user_message: str | None = Field(default=None, max_length=20_000)
    tool_name: Literal[
        "list_sources",
        "read_source",
        "read_job_source",
        "configure",
        "manage_session",
        "run_job",
        "manage_job",
    ]
    arguments: (
        ReadSourceArguments
        | ReadJobSourceArguments
        | ListSourcesArguments
        | ConfigureArguments
        | SessionActionArguments
        | RunJobArguments
        | ManageJobArguments
    )

    @model_validator(mode="after")
    def arguments_match_tool(self) -> ToolCallRequest:
        expected = {
            "list_sources": ListSourcesArguments,
            "read_source": ReadSourceArguments,
            "read_job_source": ReadJobSourceArguments,
            "configure": ConfigureArguments,
            "manage_session": SessionActionArguments,
            "run_job": RunJobArguments,
            "manage_job": ManageJobArguments,
        }[self.tool_name]
        if not isinstance(self.arguments, expected):
            raise ValueError(f"arguments do not match {self.tool_name}")
        if self.tool_name in {"configure", "manage_session", "run_job", "manage_job"} and (
            self.cancellation_id is None or self.turn_id is None
        ):
            raise ValueError(f"{self.tool_name} requires cancellation_id and turn_id")
        requires_user_message = self.tool_name == "run_job" or (
            self.tool_name == "manage_job"
            and isinstance(self.arguments, ManageJobArguments)
            and self.arguments.operation == "retry"
        )
        if requires_user_message and not self.user_message:
            raise ValueError(f"{self.tool_name} requires the current user_message")
        return self


class ToolCallResponse(BaseModel):
    model_content: str = Field(serialization_alias="modelContent")
    details: dict[str, str]


class JobSummaryResponse(BaseModel):
    job_id: str
    session_id: str
    generation: int
    status: Literal["queued", "running", "waiting", "succeeded", "failed", "cancelled"]
    notification_policy: Literal["immediate", "next_safe_pause", "silent", "user_waiting"]
    priority: int
    budget_usd: float | None
    max_runtime_seconds: int
    revision: int
    cancel_requested: bool
    attempt: int
    created_at: datetime
    updated_at: datetime


class JobListResponse(BaseModel):
    items: tuple[JobSummaryResponse, ...]


class JobEventResponse(BaseModel):
    event_id: str
    delivery_sequence: int
    job_id: str
    revision: int
    kind: Literal[
        "accepted",
        "started",
        "lease_recovered",
        "progress",
        "waiting",
        "follow_up_added",
        "priority_changed",
        "cancellation_requested",
        "cancelled",
        "succeeded",
        "failed",
    ]
    status: Literal["queued", "running", "waiting", "succeeded", "failed", "cancelled"]
    notification_policy: Literal["immediate", "next_safe_pause", "silent", "user_waiting"]
    priority: int
    detail_code: str
    entity_ids: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    occurred_at: datetime


class JobEventListResponse(BaseModel):
    items: tuple[JobEventResponse, ...]


class JobCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["follow_up", "set_priority", "cancel", "retry"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    instruction: str | None = Field(default=None, min_length=1, max_length=4_000)
    priority: int | None = Field(default=None, ge=0, le=4)

    @model_validator(mode="after")
    def valid_operation_shape(self) -> JobCommandRequest:
        if (self.operation == "follow_up") != (self.instruction is not None):
            raise ValueError("only follow_up requires an instruction")
        if (self.operation == "set_priority") != (self.priority is not None):
            raise ValueError("only set_priority requires a priority")
        return self


class JobMutationResponse(BaseModel):
    job: JobSummaryResponse
    replayed: bool


class JobClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_owner: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    lease_seconds: int = Field(default=30, ge=1, le=300)


class JobFollowUpResponse(BaseModel):
    follow_up_id: str
    instruction: str
    created_at: datetime


class JobClaimResponse(BaseModel):
    job: JobSummaryResponse
    instruction: str
    follow_ups: tuple[JobFollowUpResponse, ...]
    recovered: bool


class JobHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_owner: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    lease_seconds: int = Field(default=30, ge=1, le=300)


class JobHeartbeatResponse(BaseModel):
    job: JobSummaryResponse
    follow_ups: tuple[JobFollowUpResponse, ...]


class JobExecutorEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_owner: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    kind: Literal["progress", "waiting", "succeeded", "failed", "cancelled"]
    detail_code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    entity_ids: tuple[str, ...] = Field(default=(), max_length=50)
    artifact_ids: tuple[str, ...] = Field(default=(), max_length=50)


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


class AgentControlConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_proto_base64: str = Field(min_length=1, max_length=32768)
    participant_identity: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,96}$")


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
    model_pricing: ModelPricingRegistry
    devices: DeviceRegistry
    readiness: ReadinessProbe
    telemetry: TelemetrySink
    configuration: ConfigurationRegistry
    agent_controls: AgentControlBoundary
    configuration_sequences: dict[str, int]
    configuration_requests: dict[str, protocol_pb2.AgentControlActionRequest]
    session_transitions: SessionTransitionStore
    session_action_sequences: dict[str, int]
    session_action_requests: dict[str, protocol_pb2.AgentControlActionRequest]
    jobs: JobStore
    job_executor: JobExecutorSupervisor | None


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


def _job_summary(record: JobRecord) -> JobSummaryResponse:
    return JobSummaryResponse(
        job_id=record.job_id,
        session_id=record.session_id,
        generation=record.generation,
        status=record.status.value,
        notification_policy=record.notification_policy.value,
        priority=record.priority,
        budget_usd=(
            round(record.budget_microusd / 1_000_000, 6)
            if record.budget_microusd is not None
            else None
        ),
        max_runtime_seconds=record.max_runtime_seconds,
        revision=record.revision,
        cancel_requested=record.cancel_requested_at is not None,
        attempt=record.attempt,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _job_event(event: JobEventRecord) -> JobEventResponse:
    return JobEventResponse(
        event_id=event.event_id,
        delivery_sequence=event.delivery_sequence,
        job_id=event.job_id,
        revision=event.revision,
        kind=event.kind.value,
        status=event.status.value,
        notification_policy=event.notification_policy.value,
        priority=event.priority,
        detail_code=event.detail_code,
        entity_ids=event.entity_ids,
        artifact_ids=event.artifact_ids,
        occurred_at=event.occurred_at,
    )


def _job_tool_response(
    result: JobMutationResult,
    promoted_result: PromotedJobResult | None = None,
    latest_event: JobEventRecord | None = None,
    readable_sources: tuple[PromotedSource, ...] | None = None,
) -> ToolCallResponse:
    summary = _job_summary(result.record)
    payload: dict[str, object] = {
        "jobId": summary.job_id,
        "status": summary.status,
        "notificationPolicy": summary.notification_policy,
        "priority": summary.priority,
        "revision": summary.revision,
        "cancelRequested": summary.cancel_requested,
        "replayed": result.replayed,
    }
    if promoted_result is not None:
        sources = readable_sources if readable_sources is not None else promoted_result.sources
        promoted_payload: dict[str, object] = {
            "summary": promoted_result.summary,
            "artifacts": [
                {
                    "artifactId": artifact.artifact_id,
                    "path": artifact.path,
                    "sha256": artifact.sha256,
                    "byteLength": artifact.byte_length,
                }
                for artifact in promoted_result.artifacts
            ],
        }
        if sources:
            promoted_payload["sources"] = [
                {
                    "entityId": source.entity_id,
                    "artifactId": source.artifact_id,
                    "name": source.name,
                    "locator": source.locator,
                    "path": source.path,
                    "contentSha256": source.sha256,
                }
                for source in sources
            ]
        if promoted_result.findings:
            promoted_payload["findings"] = [
                {
                    "entityId": finding.entity_id,
                    "text": finding.text,
                    "sourceEntityIds": list(finding.source_entity_ids),
                    "inference": finding.inference,
                }
                for finding in promoted_result.findings
            ]
        if promoted_result.related_links:
            promoted_payload["relatedLinks"] = [
                {"label": item.label, "locator": item.locator}
                for item in promoted_result.related_links
            ]
        payload["result"] = promoted_payload
    if latest_event is not None:
        payload["latestEvent"] = {
            "kind": latest_event.kind.value,
            "status": latest_event.status.value,
            "detailCode": latest_event.detail_code,
            "revision": latest_event.revision,
        }
    model_content = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ToolCallResponse(
        model_content=model_content,
        details={
            "jobId": summary.job_id,
            "status": summary.status,
            "revision": str(summary.revision),
            "replayed": str(result.replayed).lower(),
            "artifactCount": str(len(promoted_result.artifacts) if promoted_result else 0),
            "sourceCount": str(
                len(readable_sources)
                if readable_sources is not None
                else len(promoted_result.sources)
                if promoted_result
                else 0
            ),
            "findingCount": str(len(promoted_result.findings) if promoted_result else 0),
            "latestEventKind": latest_event.kind.value if latest_event else "",
            "latestDetailCode": latest_event.detail_code if latest_event else "",
        },
    )


def _job_source_reference(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


async def _resolve_job_source(
    active: Runtime,
    *,
    session_id: str,
    reference: str,
    job_id: str | None = None,
) -> PromotedSourceFile:
    jobs = await active.jobs.list_for_session(
        principal_id=active.settings.owner_id,
        session_id=session_id,
        limit=100,
    )
    exact_reference: list[PromotedSourceFile] = []
    exact_path: list[PromotedSourceFile] = []
    exact_name: list[PromotedSourceFile] = []
    partial_reference: list[PromotedSourceFile] = []
    scoped_sources: list[PromotedSourceFile] = []
    normalized = _job_source_reference(reference)
    for job in jobs:
        if job_id is not None and job.job_id != job_id:
            continue
        if job.attempt < 1:
            continue
        for promoted in read_promoted_sources(
            active.settings.job_workspace_root,
            job_id=job.job_id,
            attempt=job.attempt,
        ):
            scoped_sources.append(promoted)
            source = promoted.source
            source_name = _job_source_reference(source.name)
            source_path = _job_source_reference(source.path)
            source_filename = _job_source_reference(source.path.rsplit("/", 1)[-1])
            if reference in {source.entity_id, source.artifact_id}:
                exact_reference.append(promoted)
            elif normalized and normalized in {source_path, source_filename}:
                exact_path.append(promoted)
            elif normalized and normalized == source_name:
                exact_name.append(promoted)
            elif normalized and any(
                normalized in candidate or candidate in normalized
                for candidate in {source_name, source_path, source_filename}
            ):
                partial_reference.append(promoted)
    matches = exact_reference or exact_path or exact_name or partial_reference
    if not matches and job_id is not None:
        matches = scoped_sources
    unique = {item.source.entity_id: item for item in matches}
    if not unique:
        raise JobNotFoundError("promoted job source not found")
    if len(unique) > 1:
        names = sorted({item.source.name for item in unique.values()}, key=str.casefold)[:5]
        raise JobConflictError("promoted job source reference is ambiguous: " + "; ".join(names))
    return next(iter(unique.values()))


async def _resolve_job_artifact(
    active: Runtime,
    *,
    session_id: str,
    reference: str,
    job_id: str | None = None,
) -> tuple[JobRecord, PromotedArtifactFile]:
    jobs = await active.jobs.list_for_session(
        principal_id=active.settings.owner_id,
        session_id=session_id,
        limit=100,
    )
    exact_id: list[tuple[JobRecord, PromotedArtifactFile]] = []
    exact_name: list[tuple[JobRecord, PromotedArtifactFile]] = []
    partial_name: list[tuple[JobRecord, PromotedArtifactFile]] = []
    normalized = _job_source_reference(reference)
    for job in jobs:
        if job_id is not None and job.job_id != job_id:
            continue
        if job.status is not JobStatus.SUCCEEDED or job.attempt < 1:
            continue
        for promoted in read_promoted_artifacts(
            active.settings.job_workspace_root,
            job_id=job.job_id,
            attempt=job.attempt,
        ):
            artifact = promoted.artifact
            artifact_name = _job_source_reference(artifact.path)
            candidate = (job, promoted)
            if reference == artifact.artifact_id:
                exact_id.append(candidate)
            elif normalized and normalized == artifact_name:
                exact_name.append(candidate)
            elif normalized and (normalized in artifact_name or artifact_name in normalized):
                partial_name.append(candidate)
    matches = exact_id or exact_name or partial_name
    unique = {
        (job.job_id, item.artifact.artifact_id, item.artifact.path): (job, item)
        for job, item in matches
    }
    if not unique:
        raise JobNotFoundError("promoted job artifact not found")
    if len(unique) > 1:
        names = sorted(
            {item.artifact.path for _job, item in unique.values()},
            key=str.casefold,
        )[:5]
        raise JobConflictError("promoted job artifact reference is ambiguous: " + "; ".join(names))
    return next(iter(unique.values()))


def _staged_job_input_name(position: int, original_name: str) -> str:
    basename = original_name.replace("\\", "/").rsplit("/", 1)[-1]
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", basename).strip(".-")
    if not normalized:
        normalized = "input"
    prefix = f"input-{position + 1}-"
    return prefix + normalized[: 128 - len(prefix)]


async def _resolve_job_inputs(
    active: Runtime,
    request: ToolCallRequest,
    specifications: tuple[RunJobInputArguments, ...],
) -> tuple[JobInput, ...]:
    if not specifications:
        return ()
    session = active.sessions.get(request.session_id)
    if session is None:
        raise ToolDeniedError("session source grant is unavailable")
    inputs: list[JobInput] = []
    for position, specification in enumerate(specifications):
        if specification.kind == "source":
            snapshot = await active.gateway.snapshot_source(
                session_id=request.session_id,
                generation=request.generation,
                grant=session.grant,
                reference=specification.reference,
            )
            inputs.append(
                JobInput(
                    kind="source",
                    name=_staged_job_input_name(position, snapshot.name),
                    source_session_id=request.session_id,
                    source_reference=snapshot.reference,
                    sha256=snapshot.sha256,
                    byte_length=len(snapshot.canonical_bytes),
                    canonical_bytes=snapshot.canonical_bytes,
                )
            )
            continue
        producer, promoted = await _resolve_job_artifact(
            active,
            session_id=request.session_id,
            reference=specification.reference,
            job_id=specification.job_id,
        )
        artifact = promoted.artifact
        inputs.append(
            JobInput(
                kind="artifact",
                name=_staged_job_input_name(position, artifact.path),
                source_session_id=request.session_id,
                source_reference=f"{producer.job_id}:{artifact.path}",
                producer_job_id=producer.job_id,
                artifact_id=artifact.artifact_id,
                sha256=artifact.sha256,
                byte_length=artifact.byte_length,
                canonical_bytes=promoted.canonical_bytes,
            )
        )
    return tuple(inputs)


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


async def _execute_run_job_tool(
    active: Runtime,
    request: ToolCallRequest,
    arguments: RunJobArguments,
) -> ToolCallResponse:
    budget_microusd = (
        round(arguments.budget_usd * 1_000_000) if arguments.budget_usd is not None else None
    )
    inputs = await _resolve_job_inputs(active, request, arguments.inputs)
    result = await active.jobs.accept(
        principal_id=active.settings.owner_id,
        session_id=request.session_id,
        generation=request.generation,
        idempotency_key=request.idempotency_key,
        instruction=arguments.instruction,
        user_message=request.user_message or "",
        related_links=await _related_links_for_session(active, request.session_id),
        inputs=inputs,
        notification_policy=JobNotificationPolicy(arguments.notification_policy),
        priority=arguments.priority,
        budget_microusd=budget_microusd,
        max_runtime_seconds=active.settings.job_executor_max_runtime_seconds,
    )
    return _job_tool_response(result)


async def _execute_manage_job_tool(
    active: Runtime,
    request: ToolCallRequest,
    arguments: ManageJobArguments,
) -> ToolCallResponse:
    record = await active.jobs.get(
        principal_id=active.settings.owner_id,
        job_id=arguments.job_id,
    )
    if record is None or record.session_id != request.session_id:
        raise JobNotFoundError("background job not found")
    if arguments.operation == "status":
        promoted_result = read_promoted_result(
            active.settings.job_workspace_root,
            job_id=record.job_id,
            attempt=record.attempt,
        )
        events = await active.jobs.events(record.job_id)
        readable_sources = tuple(
            item.source
            for item in read_promoted_sources(
                active.settings.job_workspace_root,
                job_id=record.job_id,
                attempt=record.attempt,
            )
        )
        return _job_tool_response(
            JobMutationResult(record=record, replayed=False),
            promoted_result,
            events[-1] if events else None,
            readable_sources,
        )
    if arguments.operation == "follow_up":
        assert arguments.instruction is not None
        result = await active.jobs.add_follow_up(
            principal_id=active.settings.owner_id,
            job_id=record.job_id,
            instruction=arguments.instruction,
            idempotency_key=request.idempotency_key,
        )
    elif arguments.operation == "set_priority":
        assert arguments.priority is not None
        result = await active.jobs.set_priority(
            principal_id=active.settings.owner_id,
            job_id=record.job_id,
            priority=arguments.priority,
            idempotency_key=request.idempotency_key,
        )
    elif arguments.operation == "cancel":
        result = await active.jobs.request_cancel(
            principal_id=active.settings.owner_id,
            job_id=record.job_id,
            idempotency_key=request.idempotency_key,
        )
    else:
        result = await active.jobs.retry(
            principal_id=active.settings.owner_id,
            job_id=record.job_id,
            idempotency_key=request.idempotency_key,
            user_message=request.user_message or "",
            related_links=await _related_links_for_session(active, request.session_id),
        )
    return _job_tool_response(result)


async def _related_links_for_session(
    active: Runtime,
    session_id: str,
) -> tuple[JobRelatedLink, ...]:
    records = await active.jobs.list_for_session(
        principal_id=active.settings.owner_id,
        session_id=session_id,
        limit=10,
    )
    links: list[JobRelatedLink] = []
    seen: set[str] = set()
    total_characters = 0
    for record in records:
        if record.status is not JobStatus.SUCCEEDED:
            continue
        result = read_promoted_result(
            active.settings.job_workspace_root,
            job_id=record.job_id,
            attempt=record.attempt,
        )
        if result is None:
            continue
        for item in result.related_links:
            if item.locator in seen:
                continue
            size = len(item.label) + len(item.locator)
            if total_characters + size > 32_000:
                return tuple(links)
            links.append(JobRelatedLink(label=item.label, locator=item.locator))
            seen.add(item.locator)
            total_characters += size
            if len(links) >= 100:
                return tuple(links)
    return tuple(links)


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


_SESSION_ACTION_IDS = {
    SessionActionKind.CREATE: protocol_pb2.AGENT_CONTROL_ACTION_ID_SESSION_CREATE,
    SessionActionKind.SWITCH: protocol_pb2.AGENT_CONTROL_ACTION_ID_SESSION_SWITCH,
    SessionActionKind.RESTART: protocol_pb2.AGENT_CONTROL_ACTION_ID_SESSION_RESTART,
    SessionActionKind.END: protocol_pb2.AGENT_CONTROL_ACTION_ID_SESSION_END,
}


def _session_action_kind_from_request(
    request: protocol_pb2.AgentControlActionRequest,
) -> SessionActionKind:
    mapping = {action_id: kind for kind, action_id in _SESSION_ACTION_IDS.items()}
    try:
        return mapping[request.action_id]
    except KeyError as error:
        raise SessionActionError("session action is not registered") from error


def _session_action_request(
    request: ToolCallRequest,
    arguments: SessionActionArguments,
    sequence: int,
    *,
    destination_session_id: str | None = None,
    requested_at: Timestamp | None = None,
) -> protocol_pb2.AgentControlActionRequest:
    kind = SessionActionKind(arguments.operation)
    timestamp = Timestamp()
    if requested_at is None:
        timestamp.GetCurrentTime()
    else:
        timestamp.CopyFrom(requested_at)
    action = protocol_pb2.AgentControlActionRequest(
        schema_version=1,
        request_id=f"session:{request.call_id}",
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
            scope=protocol_pb2.AGENT_CONTROL_SCOPE_SESSION,
            session_id=request.session_id,
        ),
        action_id=_SESSION_ACTION_IDS[kind],
        request_sequence=sequence,
        idempotency_key=request.idempotency_key,
        confirmation=(
            protocol_pb2.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_NONE
            if kind is SessionActionKind.CREATE
            else protocol_pb2.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_USER
        ),
        requested_at=timestamp,
    )
    if kind is SessionActionKind.CREATE:
        action.session_create.SetInParent()
    elif kind is SessionActionKind.SWITCH:
        action.session_switch.destination_session_id = destination_session_id or ""
    elif kind is SessionActionKind.RESTART:
        action.session_restart.SetInParent()
    else:
        action.session_end.SetInParent()
    return action


def _session_action_grant(
    request: protocol_pb2.AgentControlActionRequest,
    *,
    actor_id: str,
    origin: int,
) -> AgentControlGrant:
    return AgentControlGrant(
        grant_id=f"session-action:{request.operation.session_id}:{request.operation.generation}",
        session_id=request.operation.session_id,
        generation=request.operation.generation,
        actor_id=actor_id,
        allowed_origins=frozenset({origin}),
        allowed_actions=frozenset(_SESSION_ACTION_IDS.values()),
        allowed_scopes=frozenset({protocol_pb2.AGENT_CONTROL_SCOPE_SESSION}),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def _session_transition_response(result: SessionTransitionResult) -> ToolCallResponse:
    payload = {
        "operation": result.action.value,
        "status": "applied",
        "request_id": result.request_id,
        "source_session_id": result.source_session_id,
        "destination_session_id": result.destination_session_id,
        "replayed": result.replayed,
    }
    return ToolCallResponse(
        model_content=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        details={
            "sessionAction": result.action.value,
            "sessionTransitionStatus": "applied",
            "requestId": result.request_id,
            "sourceSessionId": result.source_session_id,
            "destinationSessionId": result.destination_session_id or "",
            "replayed": str(result.replayed).lower(),
        },
    )


async def _apply_session_transition(
    active: Runtime,
    request: protocol_pb2.AgentControlActionRequest,
    request_sha256: bytes,
) -> SessionTransitionResult:
    replay = await active.session_transitions.replay(
        request_id=request.request_id,
        idempotency_key=request.idempotency_key,
        request_sha256=request_sha256,
    )
    if replay is not None:
        active.sessions.pop(request.operation.session_id, None)
        return replay

    kind = _session_action_kind_from_request(request)
    source = await active.catalog.get(
        principal_id=active.settings.owner_id,
        session_id=request.operation.session_id,
    )
    if (
        source is None
        or source.status != "active"
        or source.generation != request.operation.generation
    ):
        raise SessionActionConflictError("originating session generation is no longer active")

    destination: SessionRecord | None = None
    previous_destination_status: str | None = None
    if kind in {SessionActionKind.CREATE, SessionActionKind.RESTART}:
        destination, _created = await active.catalog.create(
            principal_id=active.settings.owner_id,
            idempotency_key=f"agent-transition:{request.idempotency_key}",
        )
        previous_destination_status = destination.status
        if destination.status != "active":
            reactivated = await active.catalog.update(
                principal_id=active.settings.owner_id,
                session_id=destination.session_id,
                title=destination.title,
                status="active",
            )
            assert reactivated is not None
            destination = reactivated
    elif kind is SessionActionKind.SWITCH:
        destination = await active.catalog.get(
            principal_id=active.settings.owner_id,
            session_id=request.session_switch.destination_session_id,
        )
        if (
            destination is None
            or destination.status == "archived"
            or destination.session_id == source.session_id
        ):
            raise SessionActionError("destination session is unavailable")
        previous_destination_status = destination.status
        if destination.status != "active":
            reactivated = await active.catalog.update(
                principal_id=active.settings.owner_id,
                session_id=destination.session_id,
                title=destination.title,
                status="active",
            )
            assert reactivated is not None
            destination = reactivated

    if destination is not None:
        try:
            if active.workers is not None and destination.worker_mode == "managed":
                destination = await active.workers.ensure(destination)
        except WorkerLaunchError as error:
            restored_status = (
                previous_destination_status
                if previous_destination_status in {"ended", "archived"}
                else "ended"
            )
            await active.catalog.update(
                principal_id=active.settings.owner_id,
                session_id=destination.session_id,
                title=destination.title,
                status=restored_status,
            )
            if active.workers is not None:
                await active.workers.stop(destination.session_id)
            raise HTTPException(
                status_code=503,
                detail="media worker failed to start",
            ) from error

    result = await active.session_transitions.commit(
        request_id=request.request_id,
        idempotency_key=request.idempotency_key,
        request_sha256=request_sha256,
        principal_id=active.settings.owner_id,
        source_session_id=source.session_id,
        source_generation=source.generation,
        action=kind,
        destination_session_id=destination.session_id if destination is not None else None,
    )
    active.sessions.pop(source.session_id, None)
    return result


async def _execute_session_action_tool(
    active: Runtime,
    request: ToolCallRequest,
    arguments: SessionActionArguments,
) -> ToolCallResponse:
    previous = next(
        (
            candidate
            for candidate in active.session_action_requests.values()
            if candidate.idempotency_key == request.idempotency_key
        ),
        None,
    )
    destination_session_id: str | None = None
    if previous is not None and previous.action_id == (
        protocol_pb2.AGENT_CONTROL_ACTION_ID_SESSION_SWITCH
    ):
        destination_session_id = previous.session_switch.destination_session_id
    elif arguments.operation == "switch":
        assert arguments.reference is not None
        resolution = await resolve_session_reference(
            active.catalog,
            principal_id=active.settings.owner_id,
            current_session_id=request.session_id,
            reference=arguments.reference,
        )
        if resolution.status is not SessionResolutionStatus.RESOLVED:
            choices = [
                {
                    "session_id": choice.session_id,
                    "title": choice.title,
                    "last_activity_at": choice.last_activity_at.isoformat(),
                }
                for choice in resolution.choices
            ]
            clarification_payload: dict[str, object] = {
                "operation": "switch",
                "status": resolution.status.value,
                "choices": choices,
            }
            return ToolCallResponse(
                model_content=json.dumps(
                    clarification_payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                details={
                    "sessionAction": "switch",
                    "sessionTransitionStatus": resolution.status.value,
                    "choicesJson": json.dumps(choices, sort_keys=True, separators=(",", ":")),
                },
            )
        assert resolution.destination is not None
        destination_session_id = resolution.destination.session_id

    if previous is None:
        sequence = active.session_action_sequences.get(request.session_id, 0) + 1
        active.session_action_sequences[request.session_id] = sequence
        requested_at = None
    else:
        sequence = previous.request_sequence
        requested_at = previous.requested_at
    action = _session_action_request(
        request,
        arguments,
        sequence,
        destination_session_id=destination_session_id,
        requested_at=requested_at,
    )
    grant = _session_action_grant(
        action,
        actor_id="agent-sidecar",
        origin=protocol_pb2.SOURCE_KIND_CALL,
    )
    decision = active.agent_controls.submit(
        action,
        grant=grant,
        current_generation=request.generation,
    )
    if decision.result.disposition not in {
        protocol_pb2.AGENT_CONTROL_DISPOSITION_AUTHORIZED,
        protocol_pb2.AGENT_CONTROL_DISPOSITION_AWAITING_CONFIRMATION,
    }:
        raise HTTPException(
            status_code=409,
            detail=f"session action rejected ({decision.result.rejection_code})",
        )
    active.session_action_requests[action.request_id] = action
    if decision.result.disposition == (
        protocol_pb2.AGENT_CONTROL_DISPOSITION_AWAITING_CONFIRMATION
    ):
        encoded = base64.b64encode(action.SerializeToString(deterministic=True)).decode("ascii")
        kind = _session_action_kind_from_request(action)
        pending_payload: dict[str, object] = {
            "operation": kind.value,
            "status": "awaiting_confirmation",
            "request_id": action.request_id,
            "destination_session_id": destination_session_id,
        }
        return ToolCallResponse(
            model_content=json.dumps(pending_payload, sort_keys=True, separators=(",", ":")),
            details={
                "sessionAction": kind.value,
                "sessionTransitionStatus": "awaiting_confirmation",
                "requestId": action.request_id,
                "destinationSessionId": destination_session_id or "",
                "actionRequestProtoBase64": encoded,
            },
        )
    try:
        result = await _apply_session_transition(active, action, decision.result.request_sha256)
    except SessionActionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SessionActionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _session_transition_response(result)


async def _completed_session_action_replay(
    active: Runtime,
    request: ToolCallRequest,
    arguments: SessionActionArguments,
) -> ToolCallResponse | None:
    result = await active.session_transitions.find(
        request_id=f"session:{request.call_id}",
        idempotency_key=request.idempotency_key,
    )
    if result is None:
        return None
    if (
        result.request_id != f"session:{request.call_id}"
        or result.idempotency_key != request.idempotency_key
        or result.source_session_id != request.session_id
        or result.source_generation != request.generation
        or result.action.value != arguments.operation
    ):
        raise HTTPException(status_code=409, detail="session action replay changed parameters")
    if result.action is SessionActionKind.SWITCH:
        assert arguments.reference is not None
        if arguments.reference.strip() != result.destination_session_id:
            resolution = await resolve_session_reference(
                active.catalog,
                principal_id=active.settings.owner_id,
                current_session_id=request.session_id,
                reference=arguments.reference,
            )
            if (
                resolution.status is not SessionResolutionStatus.RESOLVED
                or resolution.destination is None
                or resolution.destination.session_id != result.destination_session_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="session action replay changed parameters",
                )
    return _session_transition_response(result)


async def _confirm_session_action(
    active: Runtime,
    request: AgentControlConfirmationRequest,
) -> ToolCallResponse:
    try:
        raw = base64.b64decode(request.confirmation_proto_base64, validate=True)
        confirmation = protocol_pb2.AgentControlConfirmation.FromString(raw)
    except (ValueError, DecodeError) as error:
        raise HTTPException(status_code=422, detail="invalid agent-control confirmation") from error
    pending = active.session_action_requests.get(confirmation.request_id)
    if pending is None:
        raise HTTPException(status_code=409, detail="session action confirmation expired")
    device = await active.devices.get_by_participant_identity(
        owner_id=active.settings.owner_id,
        participant_identity=request.participant_identity,
    )
    if (
        device is None
        or device.revoked_at is not None
        or confirmation.origin.actor_id != request.participant_identity
    ):
        raise HTTPException(status_code=403, detail="session action confirmation is unauthorized")
    grant = _session_action_grant(
        pending,
        actor_id=request.participant_identity,
        origin=protocol_pb2.SOURCE_KIND_USER,
    )
    decision = active.agent_controls.confirm(
        confirmation,
        grant=grant,
        current_generation=pending.operation.generation,
    )
    if decision.result.disposition == protocol_pb2.AGENT_CONTROL_DISPOSITION_CANCELLED:
        kind = _session_action_kind_from_request(pending)
        return ToolCallResponse(
            model_content=json.dumps(
                {"operation": kind.value, "status": "cancelled"},
                sort_keys=True,
                separators=(",", ":"),
            ),
            details={
                "sessionAction": kind.value,
                "sessionTransitionStatus": "cancelled",
                "requestId": pending.request_id,
            },
        )
    if decision.result.disposition != protocol_pb2.AGENT_CONTROL_DISPOSITION_AUTHORIZED:
        raise HTTPException(
            status_code=409,
            detail=f"session action confirmation rejected ({decision.result.rejection_code})",
        )
    try:
        result = await _apply_session_transition(
            active,
            pending,
            decision.result.request_sha256,
        )
    except SessionActionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SessionActionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _session_transition_response(result)


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
    job_launcher: JobLauncher | None = None,
    context_usage_client: ContextUsageClient | None = None,
    readiness_probe: ReadinessProbe | None = None,
) -> FastAPI:
    runtime: Runtime | None = None
    settings = provided_settings or Settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        nonlocal runtime
        store = EventStore(settings.database_path)
        await store.migrate()
        catalog = SessionCatalog(settings.database_path)
        jobs = JobStore(settings.database_path)
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
        job_executor: JobExecutorSupervisor | None = None
        if settings.job_executor_enabled:
            if job_launcher is not None:
                active_job_launcher = job_launcher
            else:
                command = tuple(shlex.split(settings.job_executor_command))
                configured_backend = (
                    "claude-agent-sdk"
                    if "radcliffe_control.agent_sdk_executor" in command
                    else "custom-loop"
                )
                if configured_backend != settings.job_executor_backend:
                    raise JobExecutorConfigurationError(
                        "job executor command backend does not match protected settings"
                    )
                try:
                    provider_index = command.index("--provider")
                    configured_provider = command[provider_index + 1]
                except (ValueError, IndexError) as error:
                    raise JobExecutorConfigurationError(
                        "job executor command must select an explicit model provider"
                    ) from error
                if configured_provider != settings.job_model_provider:
                    raise JobExecutorConfigurationError(
                        "job executor command provider does not match protected settings"
                    )
                try:
                    model_index = command.index("--model")
                    configured_model = command[model_index + 1]
                except (ValueError, IndexError) as error:
                    raise JobExecutorConfigurationError(
                        "job executor command must select an explicit model"
                    ) from error
                expected_model = settings.job_model_id or (
                    "claude-haiku-4-5-20251001"
                    if settings.job_model_provider == "anthropic"
                    else "qwen3:8b"
                    if settings.job_model_provider == "ollama"
                    else ""
                )
                if configured_model != expected_model:
                    raise JobExecutorConfigurationError(
                        "job executor command model does not match protected settings"
                    )
                provider_environment: dict[str, str] = {}
                if settings.job_model_provider == "anthropic":
                    if not settings.job_allow_cloud_models or not settings.anthropic_api_key:
                        raise JobExecutorConfigurationError(
                            "Anthropic background jobs require explicit cloud approval and a key"
                        )
                    provider_environment["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
                active_job_launcher = JsonLineJobLauncher(
                    command=command,
                    workspace_root=settings.job_workspace_root,
                    max_output_bytes=settings.job_executor_max_output_bytes,
                    provider_environment=provider_environment,
                )
            job_executor = JobExecutorSupervisor(
                store=jobs,
                launcher=active_job_launcher,
                max_concurrency=settings.job_executor_max_concurrency,
                poll_seconds=settings.job_executor_poll_seconds,
                heartbeat_seconds=settings.job_executor_heartbeat_seconds,
                lease_seconds=settings.job_executor_lease_seconds,
                stop_timeout_seconds=settings.job_executor_stop_timeout_seconds,
                max_output_bytes=settings.job_executor_max_output_bytes,
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
            ModelPricingRegistry.load(settings.model_pricing_path),
            DeviceRegistry(settings.database_path),
            readiness_probe or ReadinessProbe(settings),
            TelemetrySink(settings.telemetry_path),
            ConfigurationRegistry(settings.database_path),
            AgentControlBoundary(),
            {},
            {},
            SessionTransitionStore(settings.database_path),
            {},
            {},
            jobs,
            job_executor,
        )
        await reconcile_workers(runtime)
        if workers is not None:
            runtime.worker_maintenance = asyncio.create_task(
                maintain_workers(runtime),
                name="radcliffe-worker-maintenance",
            )
        if job_executor is not None:
            job_executor.start()
        try:
            yield
        finally:
            if runtime.job_executor is not None:
                await runtime.job_executor.close()
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

    @app.get("/healthz", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok" if runtime is not None else "starting",
            build_revision=settings.build_revision,
            capabilities=("background_jobs_v1", "promoted_job_results_v1"),
            background_executor_backend=settings.job_executor_backend,
            background_model_provider=settings.job_model_provider,
            background_model_id=(
                settings.job_model_id
                or (
                    "claude-haiku-4-5-20251001"
                    if settings.job_model_provider == "anthropic"
                    else "qwen3:8b"
                    if settings.job_model_provider == "ollama"
                    else ""
                )
            ),
        )

    @app.get("/readyz", response_model=ReadinessResponse)
    async def readiness(
        response: Response,
        active: Runtime = Depends(get_runtime),  # noqa: B008
    ) -> ReadinessResponse:
        maintenance = active.worker_maintenance
        report = await active.readiness.check(
            worker_supervisor_available=active.workers is not None,
            worker_maintenance_running=maintenance is not None and not maintenance.done(),
            job_executor_available=active.job_executor is not None,
            job_executor_running=(active.job_executor is not None and active.job_executor.running),
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
        resume_cost = active.model_pricing.estimate(
            model_id=measured.model_id,
            projected_input_tokens=measured.projected_input_tokens,
            representative_output_tokens=active.settings.resume_representative_output_tokens,
            exact=measured.exact,
            overflow=measured.overflow,
        )
        return ContextUsageResponse(
            session_id=session_id,
            resume_cost=resume_cost,
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
                if envelope.sensitivity is not Sensitivity.SECRET and envelope.metadata.get(
                    TURN_EVENT_METADATA
                ) not in {
                    TURN_DISPOSITION_EVENT,
                    BACKGROUND_JOB_REFERENCE_EVENT,
                    BACKGROUND_JOB_OUTCOME_EVENT,
                }:
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
                    technical_detail_categories=_technical_detail_categories(envelope),
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

    @app.get(
        "/v1/sessions/{session_id}/jobs",
        response_model=JobListResponse,
    )
    async def list_background_jobs(
        session_id: str,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        _device: DeviceRecord = Depends(require_device),  # noqa: B008
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> JobListResponse:
        session = await active.catalog.get(
            principal_id=active.settings.owner_id,
            session_id=session_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        records = await active.jobs.list_for_session(
            principal_id=active.settings.owner_id,
            session_id=session_id,
            limit=limit,
        )
        return JobListResponse(items=tuple(_job_summary(record) for record in records))

    @app.get("/v1/jobs/{job_id}", response_model=JobSummaryResponse)
    async def get_background_job(
        job_id: str,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        _device: DeviceRecord = Depends(require_device),  # noqa: B008
    ) -> JobSummaryResponse:
        record = await active.jobs.get(
            principal_id=active.settings.owner_id,
            job_id=job_id,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="background job not found")
        return _job_summary(record)

    @app.get("/v1/jobs/{job_id}/events", response_model=JobEventListResponse)
    async def replay_background_job_events(
        job_id: str,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        _device: DeviceRecord = Depends(require_device),  # noqa: B008
        after_revision: Annotated[int, Query(ge=0)] = 0,
    ) -> JobEventListResponse:
        record = await active.jobs.get(
            principal_id=active.settings.owner_id,
            job_id=job_id,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="background job not found")
        events = await active.jobs.events(job_id, after_revision=after_revision)
        return JobEventListResponse(items=tuple(_job_event(event) for event in events))

    @app.post("/v1/jobs/{job_id}/commands", response_model=JobMutationResponse)
    async def command_background_job(
        job_id: str,
        request: JobCommandRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        _device: DeviceRecord = Depends(require_device),  # noqa: B008
    ) -> JobMutationResponse:
        try:
            if request.operation == "follow_up":
                assert request.instruction is not None
                result = await active.jobs.add_follow_up(
                    principal_id=active.settings.owner_id,
                    job_id=job_id,
                    instruction=request.instruction,
                    idempotency_key=request.idempotency_key,
                )
            elif request.operation == "set_priority":
                assert request.priority is not None
                result = await active.jobs.set_priority(
                    principal_id=active.settings.owner_id,
                    job_id=job_id,
                    priority=request.priority,
                    idempotency_key=request.idempotency_key,
                )
            elif request.operation == "cancel":
                result = await active.jobs.request_cancel(
                    principal_id=active.settings.owner_id,
                    job_id=job_id,
                    idempotency_key=request.idempotency_key,
                )
            else:
                result = await active.jobs.retry(
                    principal_id=active.settings.owner_id,
                    job_id=job_id,
                    idempotency_key=request.idempotency_key,
                )
        except JobNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (JobConflictError, JobStateError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except JobError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return JobMutationResponse(job=_job_summary(result.record), replayed=result.replayed)

    @app.post("/v1/internal/jobs/claim", response_model=JobClaimResponse | None)
    async def claim_background_job(
        request: JobClaimRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
    ) -> JobClaimResponse | None:
        _bearer(authorization, active.settings.internal_token)
        claim = await active.jobs.claim_next(
            lease_owner=request.lease_owner,
            lease_seconds=request.lease_seconds,
        )
        if claim is None:
            return None
        return JobClaimResponse(
            job=_job_summary(claim.record),
            instruction=claim.instruction,
            follow_ups=tuple(
                JobFollowUpResponse(
                    follow_up_id=item.follow_up_id,
                    instruction=item.instruction,
                    created_at=item.created_at,
                )
                for item in claim.follow_ups
            ),
            recovered=claim.recovered,
        )

    @app.post("/v1/internal/jobs/{job_id}/heartbeat", response_model=JobHeartbeatResponse)
    async def heartbeat_background_job(
        job_id: str,
        request: JobHeartbeatRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
    ) -> JobHeartbeatResponse:
        _bearer(authorization, active.settings.internal_token)
        try:
            heartbeat = await active.jobs.heartbeat(
                job_id=job_id,
                lease_owner=request.lease_owner,
                lease_seconds=request.lease_seconds,
            )
        except JobNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except JobLeaseError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return JobHeartbeatResponse(
            job=_job_summary(heartbeat.record),
            follow_ups=tuple(
                JobFollowUpResponse(
                    follow_up_id=item.follow_up_id,
                    instruction=item.instruction,
                    created_at=item.created_at,
                )
                for item in heartbeat.follow_ups
            ),
        )

    @app.post("/v1/internal/jobs/{job_id}/events", response_model=JobSummaryResponse)
    async def publish_background_job_event(
        job_id: str,
        request: JobExecutorEventRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
    ) -> JobSummaryResponse:
        _bearer(authorization, active.settings.internal_token)
        try:
            record = await active.jobs.publish(
                job_id=job_id,
                lease_owner=request.lease_owner,
                kind=JobEventKind(request.kind),
                detail_code=request.detail_code,
                entity_ids=request.entity_ids,
                artifact_ids=request.artifact_ids,
            )
        except JobNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (JobLeaseError, JobStateError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except JobError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return _job_summary(record)

    @app.post(
        "/v1/internal/agent-control/confirmations",
        response_model=ToolCallResponse,
    )
    async def confirm_agent_control_action(
        request: AgentControlConfirmationRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
    ) -> ToolCallResponse:
        _bearer(authorization, active.settings.internal_token)
        async with active.lock:
            return await _confirm_session_action(active, request)

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
            if request.tool_name == "manage_session":
                replay = await _completed_session_action_replay(
                    active,
                    request,
                    cast(SessionActionArguments, request.arguments),
                )
                if replay is not None:
                    return replay
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
                if request.tool_name == "manage_session":
                    response = await _execute_session_action_tool(
                        active,
                        request,
                        cast(SessionActionArguments, request.arguments),
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
                if request.tool_name == "run_job":
                    response = await _execute_run_job_tool(
                        active,
                        request,
                        cast(RunJobArguments, request.arguments),
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
                if request.tool_name == "manage_job":
                    response = await _execute_manage_job_tool(
                        active,
                        request,
                        cast(ManageJobArguments, request.arguments),
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
                if request.tool_name == "read_job_source":
                    arguments = cast(ReadJobSourceArguments, request.arguments)
                    promoted = await _resolve_job_source(
                        active,
                        session_id=request.session_id,
                        reference=arguments.reference,
                        job_id=arguments.job_id,
                    )
                    source_grant = CapabilityGrant(
                        grant_id=(
                            f"job-source:{request.session_id}:{request.generation}:"
                            f"{promoted.source.entity_id}"
                        ),
                        session_id=request.session_id,
                        generation=request.generation,
                        readable_roots=(promoted.root,),
                        allowed_tools=frozenset({"read_source"}),
                        expires_at=datetime.now(UTC) + timedelta(minutes=5),
                    )
                    result = await active.gateway.read_source(
                        call_id=request.call_id,
                        idempotency_key=request.idempotency_key,
                        session_id=request.session_id,
                        generation=request.generation,
                        grant=source_grant,
                        raw_arguments={
                            "path": promoted.source.path,
                            **({"rate": arguments.rate} if arguments.rate is not None else {}),
                            **(
                                {"start_at_boundary": arguments.start_at_boundary}
                                if arguments.start_at_boundary is not None
                                else {}
                            ),
                        },
                        provenance=Provenance(
                            locator=promoted.source.locator,
                            display_name=promoted.source.name,
                            extractor="promoted-job-source",
                            extractor_version="1",
                            source_sha256=bytes.fromhex(promoted.source.sha256),
                            permission_basis=source_grant.grant_id,
                        ),
                    )
                elif request.tool_name == "list_sources":
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
                else:
                    result = await active.gateway.read_source(
                        call_id=request.call_id,
                        idempotency_key=request.idempotency_key,
                        session_id=request.session_id,
                        generation=request.generation,
                        grant=grant,
                        raw_arguments=request.arguments.model_dump(exclude_none=True),
                    )
            except JobNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (JobConflictError, JobStateError, JobLeaseError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except JobError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
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
                **(
                    {"startAtBoundary": str(result.start_at_boundary)}
                    if result.start_at_boundary is not None
                    else {}
                ),
            },
        )

    return app


app = create_app()
