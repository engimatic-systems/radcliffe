from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from livekit import api
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from radcliffe_domain.catalog import (
    InvalidPageCursorError,
    SessionCatalog,
    SessionConflictError,
    SessionRecord,
)
from radcliffe_domain.projection import CONTEXT_PROJECTION_VERSION, context_replay
from radcliffe_domain.store import EventStore
from radcliffe_domain.tools import CapabilityGrant, ToolDeniedError, ToolGateway

from radcliffe_control.context_usage import (
    ContextUsageClient,
    ContextUsageClientError,
    SidecarContextUsageClient,
)
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
    role: Literal["client", "worker"] = "client"
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=120)


class SessionCredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant_identity: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,96}$")
    role: Literal["client", "worker"] = "client"


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
    expires_in_seconds: int


class SessionListResponse(BaseModel):
    items: tuple[SessionSummaryResponse, ...]
    next_cursor: str | None


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


class ReadSourceArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str


class ToolCallRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=_to_camel,
        populate_by_name=True,
    )

    session_id: str
    generation: int
    call_id: str
    idempotency_key: str
    tool_name: Literal["read_source"]
    arguments: ReadSourceArguments


class ToolCallResponse(BaseModel):
    model_content: str = Field(serialization_alias="modelContent")
    details: dict[str, str]


@dataclass(frozen=True, slots=True)
class ActiveSession:
    generation: int
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

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok" if runtime is not None else "starting"}

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
            except Exception:
                logger.exception("managed worker reconciliation failed")

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
            allowed_tools=frozenset({"read_source"}),
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
            expires_in_seconds=active.settings.room_token_ttl_seconds,
        )

    @app.post("/v1/sessions", response_model=SessionResponse)
    async def create_session(
        request: SessionRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
    ) -> SessionResponse:
        _bearer(authorization, active.settings.bootstrap_token)
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
            participant_identity=request.participant_identity,
            role=request.role,
        )

    @app.get("/v1/sessions", response_model=SessionListResponse)
    async def list_sessions(
        active: Runtime = Depends(get_runtime),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: str | None = None,
    ) -> SessionListResponse:
        _bearer(authorization, active.settings.bootstrap_token)
        try:
            page = await active.catalog.list(
                principal_id=active.settings.owner_id,
                limit=limit,
                cursor=cursor,
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
        authorization: Annotated[str | None, Header()] = None,
    ) -> SessionSummaryResponse:
        _bearer(authorization, active.settings.bootstrap_token)
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
        authorization: Annotated[str | None, Header()] = None,
    ) -> ContextUsageResponse:
        _bearer(authorization, active.settings.bootstrap_token)
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

    @app.patch("/v1/sessions/{session_id}", response_model=SessionSummaryResponse)
    async def update_session(
        session_id: str,
        request: SessionUpdateRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
    ) -> SessionSummaryResponse:
        _bearer(authorization, active.settings.bootstrap_token)
        record = await active.catalog.update(
            principal_id=active.settings.owner_id,
            session_id=session_id,
            title=request.title,
            status=request.status,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="session not found")
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
        authorization: Annotated[str | None, Header()] = None,
    ) -> SessionResponse:
        _bearer(authorization, active.settings.bootstrap_token)
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
            participant_identity=request.participant_identity,
            role=request.role,
        )

    @app.post("/v1/tool-calls", response_model=ToolCallResponse)
    async def execute_tool(
        request: ToolCallRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
    ) -> ToolCallResponse:
        _bearer(authorization, active.settings.internal_token)
        session = active.sessions.get(request.session_id)
        if session is None:
            raise HTTPException(status_code=403, detail="unknown session")
        try:
            result = await active.gateway.read_source(
                call_id=request.call_id,
                idempotency_key=request.idempotency_key,
                session_id=request.session_id,
                generation=request.generation,
                grant=session.grant,
                raw_arguments=request.arguments.model_dump(),
            )
        except ToolDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        source = result.source_envelope
        return ToolCallResponse(
            model_content=(
                f"Canonical source {source.message_id} was authorized and handed directly "
                "to Radcliffe rendering. Do not reproduce its text."
            ),
            details={
                "sourceMessageId": source.message_id,
                "contentSha256": source.content_sha256.hex(),
                "locator": source.provenance.locator,
            },
        )

    return app


app = create_app()
