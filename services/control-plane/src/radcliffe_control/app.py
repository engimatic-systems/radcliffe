from __future__ import annotations

import asyncio
import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, status
from livekit import api
from pydantic import BaseModel, ConfigDict, Field
from radcliffe_domain.store import EventStore
from radcliffe_domain.tools import CapabilityGrant, ToolDeniedError, ToolGateway

from radcliffe_control.settings import Settings


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.title() for part in rest)


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
    participant_identity: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,96}$")
    role: Literal["client", "worker"] = "client"


class SessionResponse(BaseModel):
    livekit_url: str
    room_name: str
    participant_token: str
    expires_in_seconds: int
    protocol_version: int = 2
    generation: int


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
    gateway: ToolGateway
    sessions: dict[str, ActiveSession]
    lock: asyncio.Lock


def _bearer(authorization: str | None, expected: str) -> None:
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")


def create_app(provided_settings: Settings | None = None) -> FastAPI:
    runtime: Runtime | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        nonlocal runtime
        settings = provided_settings or Settings()
        store = EventStore(settings.database_path)
        await store.migrate()
        runtime = Runtime(settings, store, ToolGateway(store), {}, asyncio.Lock())
        yield
        runtime = None

    app = FastAPI(title="Radcliffe Control Plane", version="0.1.0", lifespan=lifespan)

    def get_runtime() -> Runtime:
        if runtime is None:
            raise HTTPException(status_code=503, detail="control plane is starting")
        return runtime

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok" if runtime is not None else "starting"}

    @app.post("/v1/sessions", response_model=SessionResponse)
    async def create_session(
        request: SessionRequest,
        active: Runtime = Depends(get_runtime),  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
    ) -> SessionResponse:
        _bearer(authorization, active.settings.bootstrap_token)
        async with active.lock:
            current = active.sessions.get(request.session_id)
            generation = current.generation if current is not None else 1
            grant = CapabilityGrant(
                grant_id=f"source:{request.session_id}:{generation}",
                session_id=request.session_id,
                generation=generation,
                readable_roots=(active.settings.source_root.resolve(),),
                allowed_tools=frozenset({"read_source"}),
                expires_at=datetime.now(UTC)
                + timedelta(seconds=active.settings.room_token_ttl_seconds),
            )
            active.sessions[request.session_id] = ActiveSession(generation, grant)

        sources = ["microphone"] if request.role == "client" else None
        token = (
            api.AccessToken(
                active.settings.livekit_api_key,
                active.settings.livekit_api_secret,
            )
            .with_identity(request.participant_identity)
            .with_ttl(timedelta(seconds=active.settings.room_token_ttl_seconds))
            .with_attributes({"radcliffe.protocol": "2", "radcliffe.generation": str(generation)})
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=request.session_id,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                    can_publish_sources=sources,
                )
            )
            .to_jwt()
        )
        return SessionResponse(
            livekit_url=active.settings.livekit_url,
            room_name=request.session_id,
            participant_token=token,
            expires_in_seconds=active.settings.room_token_ttl_seconds,
            generation=generation,
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
