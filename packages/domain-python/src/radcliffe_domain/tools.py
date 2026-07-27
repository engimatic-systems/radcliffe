from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import stat
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePath

from pydantic import BaseModel, ConfigDict, Field, field_validator
from radcliffe_protocol.radcliffe.v2 import protocol_pb2

from radcliffe_domain.envelope import MessageEnvelope, Provenance, SourceKind
from radcliffe_domain.projection import SpeechDelivery, with_delivery_rate_for_all
from radcliffe_domain.store import EventStore, ToolAudit

MAX_SOURCE_BYTES = 1024 * 1024
MAX_SOURCE_CATALOG_ENTRIES = 200
MAX_SOURCE_CATALOG_DEPTH = 4
MAX_SOURCE_CATALOG_SCANNED_ENTRIES = 2000
_SPOKEN_DASH = re.compile(r"\s+(?:dash|hyphen)\s+", re.IGNORECASE)
_SPOKEN_MARKDOWN_SUFFIX = re.compile(r"\s+dot\s+m\s*d\s*$", re.IGNORECASE)


class ToolDeniedError(PermissionError):
    pass


class ToolCancelledError(asyncio.CancelledError):
    pass


class ReadSourceArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    rate: str | float | None = None

    @field_validator("rate", mode="before")
    @classmethod
    def valid_rate(cls, value: object) -> object:
        if value is not None:
            SpeechDelivery.parse(value)
        return value


class ListSourcesArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


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


@dataclass(frozen=True, slots=True)
class ListSourcesResult:
    call_envelope: MessageEnvelope
    result_envelope: MessageEnvelope
    paths: tuple[str, ...]
    truncated: bool
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    name: str
    reference: str
    sha256: str
    canonical_bytes: bytes


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
        provenance: Provenance | None = None,
    ) -> ReadSourceResult:
        arguments = ReadSourceArguments.model_validate(raw_arguments)
        arguments_json = json.dumps(
            arguments.model_dump(exclude_none=True), sort_keys=True, separators=(",", ":")
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
            self._validate_replay(existing, pending)
            return await self._replay(existing)

        call_envelope = MessageEnvelope.text(
            session_id=session_id,
            sequence=await self.store.next_sequence(session_id),
            source=SourceKind.CALL,
            text=json.dumps(
                {"path": arguments.path},
                sort_keys=True,
                separators=(",", ":"),
            ),
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
                self._authorize(session_id, generation, grant, "read_source")
                path = self._resolve(arguments.path, grant.readable_roots)
                self._check_cancelled(cancelled)
                canonical = await asyncio.to_thread(path.read_bytes)
                self._check_cancelled(cancelled)
                if len(canonical) > MAX_SOURCE_BYTES:
                    raise ToolDeniedError("source exceeds the 1 MiB limit")
                text = canonical.decode("utf-8")
                digest = hashlib.sha256(canonical).digest()
                if provenance is not None and provenance.source_sha256 not in {b"", digest}:
                    raise ToolDeniedError("promoted source no longer matches its canonical hash")
                source_provenance = (
                    provenance.model_copy(
                        update={
                            "source_sha256": digest,
                            "permission_basis": grant.grant_id,
                        }
                    )
                    if provenance is not None
                    else Provenance(
                        locator=str(path),
                        display_name=path.name,
                        extractor="utf8-markdown",
                        extractor_version="1",
                        source_sha256=digest,
                        permission_basis=grant.grant_id,
                    )
                )
                source_envelope = MessageEnvelope.text(
                    session_id=session_id,
                    sequence=await self.store.next_sequence(session_id),
                    source=SourceKind.TEXT_SOURCE,
                    text=text,
                    message_id=f"source:{call_id}",
                    metadata={"tool_name": "read_source", "call_id": call_id},
                    provenance=source_provenance,
                )
                if arguments.rate is not None:
                    source_envelope = with_delivery_rate_for_all(
                        source_envelope,
                        arguments.rate,
                    )
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
                        "source_envelope_proto_base64": base64.b64encode(
                            source_envelope.to_proto().SerializeToString(deterministic=True)
                        ).decode("ascii"),
                        "result_message_id": result_envelope.message_id,
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

    async def list_sources(
        self,
        *,
        call_id: str,
        idempotency_key: str,
        session_id: str,
        generation: int,
        grant: CapabilityGrant,
        raw_arguments: object,
        cancelled: asyncio.Event | None = None,
    ) -> ListSourcesResult:
        arguments = ListSourcesArguments.model_validate(raw_arguments)
        arguments_json = json.dumps(
            arguments.model_dump(), sort_keys=True, separators=(",", ":")
        ).encode()
        pending = ToolAudit(
            call_id=call_id,
            idempotency_key=idempotency_key,
            session_id=session_id,
            generation=generation,
            tool_name="list_sources",
            arguments_json=arguments_json,
            grant_id=grant.grant_id,
            authorization="pending",
            outcome="requested",
        )
        existing = await self.store.create_tool_audit(pending)
        if existing is not None:
            self._validate_replay(existing, pending)
            return await self._replay_catalog(existing)

        call_envelope = MessageEnvelope.text(
            session_id=session_id,
            sequence=await self.store.next_sequence(session_id),
            source=SourceKind.CALL,
            text=arguments_json.decode(),
            message_id=f"call:{call_id}",
            metadata={
                "tool_name": "list_sources",
                "call_id": call_id,
                "grant_id": grant.grant_id,
                "outcome": "requested",
            },
        )
        await self.store.append(call_envelope)

        async with self._serial:
            try:
                self._authorize(session_id, generation, grant, "list_sources")
                self._check_cancelled(cancelled)
                paths, truncated = await asyncio.to_thread(
                    self._catalog, grant.readable_roots, cancelled
                )
                self._check_cancelled(cancelled)
                catalog = json.dumps(
                    {"paths": paths, "truncated": truncated},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                result_envelope = MessageEnvelope.text(
                    session_id=session_id,
                    sequence=await self.store.next_sequence(session_id),
                    source=SourceKind.CALL,
                    text=catalog,
                    message_id=f"call-result:{call_id}",
                    metadata={
                        "tool_name": "list_sources",
                        "call_id": call_id,
                        "grant_id": grant.grant_id,
                        "outcome": "succeeded",
                    },
                )
                await self.store.append(result_envelope)
                result_json = json.dumps(
                    {
                        "call_message_id": call_envelope.message_id,
                        "result_message_id": result_envelope.message_id,
                        "paths": paths,
                        "truncated": truncated,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                await self.store.finish_tool_audit(
                    replace(
                        pending,
                        authorization="allowed",
                        outcome="succeeded",
                        resolved_path=json.dumps(
                            [str(root.resolve(strict=True)) for root in grant.readable_roots]
                        ),
                        content_sha256=hashlib.sha256(catalog.encode()).digest(),
                        result_json=result_json,
                    )
                )
                return ListSourcesResult(call_envelope, result_envelope, paths, truncated)
            except BaseException as exc:
                outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "denied"
                await self.store.finish_tool_audit(
                    replace(pending, authorization="denied", outcome=outcome)
                )
                raise

    async def snapshot_source(
        self,
        *,
        session_id: str,
        generation: int,
        grant: CapabilityGrant,
        reference: str,
    ) -> SourceSnapshot:
        """Capture an explicitly authorized source for an isolated background job."""
        async with self._serial:
            self._authorize(session_id, generation, grant, "read_source")
            path = self._resolve_non_symlink(reference, grant.readable_roots)
            canonical = await asyncio.to_thread(path.read_bytes)
            if not canonical or len(canonical) > MAX_SOURCE_BYTES:
                raise ToolDeniedError("source must contain at most 1 MiB")
            try:
                canonical.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ToolDeniedError("source is not UTF-8 text") from exc
            return SourceSnapshot(
                name=path.name,
                reference=reference,
                sha256=hashlib.sha256(canonical).hexdigest(),
                canonical_bytes=canonical,
            )

    def _authorize(
        self,
        session_id: str,
        generation: int,
        grant: CapabilityGrant,
        tool_name: str,
    ) -> None:
        now = datetime.now(UTC)
        if grant.revoked or grant.expires_at <= now:
            raise ToolDeniedError("capability grant is stale")
        if grant.session_id != session_id or grant.generation != generation:
            raise ToolDeniedError("capability grant belongs to another session generation")
        if tool_name not in grant.allowed_tools:
            raise ToolDeniedError(f"{tool_name} is not granted")

    @staticmethod
    def _catalog(
        roots: tuple[Path, ...],
        cancelled: asyncio.Event | None,
    ) -> tuple[tuple[str, ...], bool]:
        candidates: list[str] = []
        scanned = 0
        truncated = False
        scan_exhausted = False

        def visit(directory: Path, relative_parent: PurePath, depth: int) -> None:
            nonlocal scanned, scan_exhausted, truncated
            if scan_exhausted:
                return
            entries = sorted(
                directory.iterdir(), key=lambda entry: (entry.name.casefold(), entry.name)
            )
            for entry in entries:
                ToolGateway._check_cancelled(cancelled)
                scanned += 1
                if scanned > MAX_SOURCE_CATALOG_SCANNED_ENTRIES:
                    scan_exhausted = True
                    truncated = True
                    return
                try:
                    mode = entry.stat(follow_symlinks=False).st_mode
                except OSError:
                    continue
                if entry.name.startswith(".") or stat.S_ISLNK(mode):
                    continue
                relative = relative_parent / entry.name
                if stat.S_ISDIR(mode):
                    if depth < MAX_SOURCE_CATALOG_DEPTH:
                        visit(entry, relative, depth + 1)
                    continue
                if not stat.S_ISREG(mode) or entry.suffix.casefold() != ".md":
                    continue
                candidates.append(relative.as_posix())
                if len(candidates) > MAX_SOURCE_CATALOG_ENTRIES:
                    truncated = True

        for root in roots:
            resolved_root = root.resolve(strict=True)
            visit(resolved_root, PurePath(), 1)
            if scan_exhausted:
                break

        by_folded_path: dict[str, list[str]] = {}
        for path in candidates:
            by_folded_path.setdefault(path.casefold(), []).append(path)
        unique = sorted(
            (paths[0] for paths in by_folded_path.values() if len(paths) == 1),
            key=lambda path: (path.casefold(), path),
        )
        if len(unique) > MAX_SOURCE_CATALOG_ENTRIES:
            truncated = True
        return tuple(unique[:MAX_SOURCE_CATALOG_ENTRIES]), truncated

    @staticmethod
    def _validate_replay(existing: ToolAudit, requested: ToolAudit) -> None:
        immutable_fields = (
            "session_id",
            "generation",
            "tool_name",
            "arguments_json",
            "grant_id",
        )
        if any(getattr(existing, field) != getattr(requested, field) for field in immutable_fields):
            raise ToolDeniedError("tool call identifier was reused with different parameters")

    @staticmethod
    def _resolve(requested: str, roots: tuple[Path, ...]) -> Path:
        requested_paths = ToolGateway._requested_paths(requested)
        markdown_candidate = False
        for requested_path in requested_paths:
            relative = PurePath(requested_path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ToolDeniedError("path traversal is not permitted")
            if relative.suffix.lower() != ".md":
                continue
            markdown_candidate = True
            for root in roots:
                resolved_root = root.resolve(strict=True)
                try:
                    candidate = ToolGateway._resolve_beneath_root(resolved_root, relative)
                except OSError:
                    continue
                if candidate.is_file():
                    return candidate
        if not markdown_candidate:
            raise ToolDeniedError("only Markdown sources are permitted")
        raise ToolDeniedError("source is outside the readable roots")

    @staticmethod
    def _resolve_non_symlink(requested: str, roots: tuple[Path, ...]) -> Path:
        requested_paths = ToolGateway._requested_paths(requested)
        markdown_candidate = False
        for requested_path in requested_paths:
            relative = PurePath(requested_path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ToolDeniedError("path traversal is not permitted")
            if relative.suffix.lower() != ".md":
                continue
            markdown_candidate = True
            for configured_root in roots:
                try:
                    root = configured_root.resolve(strict=True)
                except OSError:
                    continue
                candidate = root
                try:
                    for part in relative.parts:
                        entries = tuple(candidate.iterdir())
                        exact = tuple(entry for entry in entries if entry.name == part)
                        matches = exact or tuple(
                            entry for entry in entries if entry.name.casefold() == part.casefold()
                        )
                        if len(matches) != 1:
                            raise FileNotFoundError(part)
                        selected = matches[0]
                        if selected.is_symlink():
                            raise ToolDeniedError("symbolic-link sources are not permitted")
                        candidate = selected
                    resolved = candidate.resolve(strict=True)
                except OSError:
                    continue
                if not resolved.is_relative_to(root):
                    raise ToolDeniedError("source is outside the readable roots")
                if resolved.is_file():
                    return resolved
        if not markdown_candidate:
            raise ToolDeniedError("only Markdown sources are permitted")
        raise ToolDeniedError("source is outside the readable roots")

    @staticmethod
    def _requested_paths(requested: str) -> tuple[str, ...]:
        literal = requested.strip()
        spoken = _SPOKEN_DASH.sub("-", literal)
        spoken = _SPOKEN_MARKDOWN_SUFFIX.sub(".md", spoken)
        return (literal,) if spoken == literal else (literal, spoken)

    @staticmethod
    def _resolve_beneath_root(root: Path, relative: PurePath) -> Path:
        """Resolve an exact or unambiguous case-insensitive path below root."""
        candidate = root
        for part in relative.parts:
            entries = tuple(candidate.iterdir())
            exact = tuple(entry for entry in entries if entry.name == part)
            matches = exact or tuple(
                entry for entry in entries if entry.name.casefold() == part.casefold()
            )
            if len(matches) != 1:
                raise FileNotFoundError(part)
            candidate = matches[0].resolve(strict=True)
            if not candidate.is_relative_to(root):
                raise ToolDeniedError("source is outside the readable roots")
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
        encoded_source = payload.get("source_envelope_proto_base64")
        if isinstance(encoded_source, str):
            source = MessageEnvelope.from_proto(
                protocol_pb2.MessageEnvelope.FromString(base64.b64decode(encoded_source))
            )
        else:
            source = indexed[payload["source_message_id"]]
        result = indexed[payload["result_message_id"]]
        return ReadSourceResult(
            call, source, result, source.content.canonical_bytes(), replayed=True
        )

    async def _replay_catalog(self, audit: ToolAudit) -> ListSourcesResult:
        if audit.outcome != "succeeded" or audit.result_json is None:
            raise ToolDeniedError(f"duplicate call has terminal outcome {audit.outcome}")
        payload = json.loads(audit.result_json)
        envelopes = await self.store.replay(audit.session_id)
        indexed = {envelope.message_id: envelope for envelope in envelopes}
        return ListSourcesResult(
            indexed[payload["call_message_id"]],
            indexed[payload["result_message_id"]],
            tuple(payload["paths"]),
            bool(payload["truncated"]),
            replayed=True,
        )
