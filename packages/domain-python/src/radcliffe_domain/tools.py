from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePath

from pydantic import BaseModel, ConfigDict, Field

from radcliffe_domain.envelope import MessageEnvelope, Provenance, SourceKind
from radcliffe_domain.store import EventStore, ToolAudit

MAX_SOURCE_BYTES = 1024 * 1024


class ToolDeniedError(PermissionError):
    pass


class ToolCancelledError(asyncio.CancelledError):
    pass


class ReadSourceArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1, max_length=4096)


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    grant_id: str
    session_id: str
    generation: int
    readable_roots: tuple[Path, ...]
    allowed_tools: frozenset[str]
    expires_at: datetime
    revoked: bool = False


@dataclass(frozen=True, slots=True)
class ReadSourceResult:
    call_envelope: MessageEnvelope
    source_envelope: MessageEnvelope
    result_envelope: MessageEnvelope
    canonical_bytes: bytes
    replayed: bool = False


class ToolGateway:
    def __init__(self, store: EventStore) -> None:
        self.store = store
        self._serial = asyncio.Lock()

    async def read_source(
        self,
        *,
        call_id: str,
        idempotency_key: str,
        session_id: str,
        generation: int,
        grant: CapabilityGrant,
        raw_arguments: object,
        cancelled: asyncio.Event | None = None,
    ) -> ReadSourceResult:
        arguments = ReadSourceArguments.model_validate(raw_arguments)
        arguments_json = json.dumps(
            arguments.model_dump(), sort_keys=True, separators=(",", ":")
        ).encode()
        pending = ToolAudit(
            call_id=call_id,
            idempotency_key=idempotency_key,
            session_id=session_id,
            generation=generation,
            tool_name="read_source",
            arguments_json=arguments_json,
            grant_id=grant.grant_id,
            authorization="pending",
            outcome="requested",
        )
        existing = await self.store.create_tool_audit(pending)
        if existing is not None:
            return await self._replay(existing)

        call_envelope = MessageEnvelope.text(
            session_id=session_id,
            sequence=await self.store.next_sequence(session_id),
            source=SourceKind.CALL,
            text=arguments_json.decode(),
            message_id=f"call:{call_id}",
            metadata={
                "tool_name": "read_source",
                "call_id": call_id,
                "grant_id": grant.grant_id,
                "outcome": "requested",
            },
        )
        await self.store.append(call_envelope)

        async with self._serial:
            try:
                self._authorize(session_id, generation, grant)
                path = self._resolve(arguments.path, grant.readable_roots)
                self._check_cancelled(cancelled)
                canonical = await asyncio.to_thread(path.read_bytes)
                self._check_cancelled(cancelled)
                if len(canonical) > MAX_SOURCE_BYTES:
                    raise ToolDeniedError("source exceeds the 1 MiB limit")
                text = canonical.decode("utf-8")
                digest = hashlib.sha256(canonical).digest()
                source_envelope = MessageEnvelope.text(
                    session_id=session_id,
                    sequence=await self.store.next_sequence(session_id),
                    source=SourceKind.TEXT_SOURCE,
                    text=text,
                    message_id=f"source:{call_id}",
                    metadata={"tool_name": "read_source", "call_id": call_id},
                    provenance=Provenance(
                        locator=str(path),
                        display_name=path.name,
                        extractor="utf8-markdown",
                        extractor_version="1",
                        source_sha256=digest,
                        permission_basis=grant.grant_id,
                    ),
                )
                await self.store.append(source_envelope)
                result_content = json.dumps(
                    {
                        "source_message_id": source_envelope.message_id,
                        "content_sha256": digest.hex(),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                result_envelope = MessageEnvelope.text(
                    session_id=session_id,
                    sequence=await self.store.next_sequence(session_id),
                    source=SourceKind.CALL,
                    text=result_content,
                    message_id=f"call-result:{call_id}",
                    metadata={
                        "tool_name": "read_source",
                        "call_id": call_id,
                        "grant_id": grant.grant_id,
                        "outcome": "succeeded",
                    },
                )
                await self.store.append(result_envelope)
                result_json = json.dumps(
                    {
                        "call_message_id": call_envelope.message_id,
                        "source_message_id": source_envelope.message_id,
                        "result_message_id": result_envelope.message_id,
                        "canonical_text": text,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                await self.store.finish_tool_audit(
                    replace(
                        pending,
                        authorization="allowed",
                        outcome="succeeded",
                        resolved_path=str(path),
                        content_sha256=digest,
                        result_json=result_json,
                    )
                )
                return ReadSourceResult(call_envelope, source_envelope, result_envelope, canonical)
            except BaseException as exc:
                outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "denied"
                await self.store.finish_tool_audit(
                    replace(pending, authorization="denied", outcome=outcome)
                )
                raise

    def _authorize(self, session_id: str, generation: int, grant: CapabilityGrant) -> None:
        now = datetime.now(UTC)
        if grant.revoked or grant.expires_at <= now:
            raise ToolDeniedError("capability grant is stale")
        if grant.session_id != session_id or grant.generation != generation:
            raise ToolDeniedError("capability grant belongs to another session generation")
        if "read_source" not in grant.allowed_tools:
            raise ToolDeniedError("read_source is not granted")

    @staticmethod
    def _resolve(requested: str, roots: tuple[Path, ...]) -> Path:
        relative = PurePath(requested)
        if relative.is_absolute() or ".." in relative.parts:
            raise ToolDeniedError("path traversal is not permitted")
        if relative.suffix.lower() != ".md":
            raise ToolDeniedError("only Markdown sources are permitted")
        for root in roots:
            resolved_root = root.resolve(strict=True)
            candidate = (resolved_root / relative).resolve(strict=True)
            if candidate.is_relative_to(resolved_root) and candidate.is_file():
                return ToolGateway._canonical_case(candidate)
        raise ToolDeniedError("source is outside the readable roots")

    @staticmethod
    def _canonical_case(candidate: Path) -> Path:
        entries = tuple(candidate.parent.iterdir())
        for entry in entries:
            if entry.name == candidate.name and entry.samefile(candidate):
                return entry.resolve(strict=True)
        for entry in entries:
            if entry.name.casefold() == candidate.name.casefold() and entry.samefile(candidate):
                return entry.resolve(strict=True)
        return candidate

    @staticmethod
    def _check_cancelled(cancelled: asyncio.Event | None) -> None:
        if cancelled is not None and cancelled.is_set():
            raise ToolCancelledError()

    async def _replay(self, audit: ToolAudit) -> ReadSourceResult:
        if audit.outcome != "succeeded" or audit.result_json is None:
            raise ToolDeniedError(f"duplicate call has terminal outcome {audit.outcome}")
        payload = json.loads(audit.result_json)
        envelopes = await self.store.replay(audit.session_id)
        indexed = {envelope.message_id: envelope for envelope in envelopes}
        call = indexed[payload["call_message_id"]]
        source = indexed[payload["source_message_id"]]
        result = indexed[payload["result_message_id"]]
        return ReadSourceResult(
            call, source, result, payload["canonical_text"].encode(), replayed=True
        )
