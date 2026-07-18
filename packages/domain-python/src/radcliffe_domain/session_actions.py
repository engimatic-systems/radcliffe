from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import aiosqlite

from radcliffe_domain.catalog import SessionCatalog, SessionRecord


class SessionActionError(ValueError):
    pass


class SessionActionConflictError(SessionActionError):
    pass


class SessionActionKind(StrEnum):
    CREATE = "create"
    SWITCH = "switch"
    RESTART = "restart"
    END = "end"


class SessionResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class SessionChoice:
    session_id: str
    title: str | None
    last_activity_at: datetime


@dataclass(frozen=True, slots=True)
class SessionResolution:
    status: SessionResolutionStatus
    destination: SessionRecord | None
    choices: tuple[SessionChoice, ...]


@dataclass(frozen=True, slots=True)
class SessionTransitionResult:
    request_id: str
    idempotency_key: str
    principal_id: str
    source_session_id: str
    source_generation: int
    action: SessionActionKind
    destination_session_id: str | None
    occurred_at: datetime
    replayed: bool = False


def session_action_kind(value: SessionActionKind | str) -> SessionActionKind:
    try:
        return SessionActionKind(value)
    except ValueError as error:
        raise SessionActionError("session action is not registered") from error


def _choice(record: SessionRecord) -> SessionChoice:
    return SessionChoice(record.session_id, record.title, record.last_activity_at)


async def resolve_session_reference(
    catalog: SessionCatalog,
    *,
    principal_id: str,
    current_session_id: str,
    reference: str,
    choice_limit: int = 3,
) -> SessionResolution:
    if not 1 <= choice_limit <= 5:
        raise SessionActionError("session choice limit must be between one and five")
    normalized = " ".join(reference.strip().casefold().split())
    if not normalized or len(normalized) > 120:
        raise SessionActionError("session reference must be between one and 120 characters")

    exact = await catalog.get(principal_id=principal_id, session_id=reference.strip())
    if exact is not None and exact.status != "archived" and exact.session_id != current_session_id:
        return SessionResolution(SessionResolutionStatus.RESOLVED, exact, ())

    page = await catalog.list(
        principal_id=principal_id,
        limit=100,
        include_archived=False,
    )
    candidates = tuple(
        record
        for record in page.items
        if record.session_id != current_session_id and record.status != "archived"
    )
    generic = normalized in {
        "last",
        "last conversation",
        "most recent",
        "previous",
        "previous conversation",
        "recent",
        "recent conversation",
    }
    if generic:
        matches = candidates
    else:
        exact_titles = tuple(
            record
            for record in candidates
            if record.title is not None and " ".join(record.title.casefold().split()) == normalized
        )
        if exact_titles:
            matches = exact_titles
        else:
            matches = tuple(
                record
                for record in candidates
                if (
                    record.title is not None
                    and normalized in " ".join(record.title.casefold().split())
                )
                or record.session_id.casefold().startswith(normalized)
            )

    if len(matches) == 1:
        return SessionResolution(SessionResolutionStatus.RESOLVED, matches[0], ())
    if matches:
        return SessionResolution(
            SessionResolutionStatus.AMBIGUOUS,
            None,
            tuple(_choice(record) for record in matches[:choice_limit]),
        )
    return SessionResolution(SessionResolutionStatus.NOT_FOUND, None, ())


class SessionTransitionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def replay(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        request_sha256: bytes,
    ) -> SessionTransitionResult | None:
        async with aiosqlite.connect(self.path) as database:
            database.row_factory = aiosqlite.Row
            row = await (
                await database.execute(
                    "SELECT * FROM session_transitions WHERE request_id = ? OR idempotency_key = ?",
                    (request_id, idempotency_key),
                )
            ).fetchone()
        if row is None:
            return None
        if bytes(row["request_sha256"]) != request_sha256:
            raise SessionActionConflictError(
                "session action idempotency key was reused with different parameters"
            )
        return replace(self._from_row(row), replayed=True)

    async def find(
        self,
        *,
        request_id: str,
        idempotency_key: str,
    ) -> SessionTransitionResult | None:
        async with aiosqlite.connect(self.path) as database:
            database.row_factory = aiosqlite.Row
            row = await (
                await database.execute(
                    "SELECT * FROM session_transitions WHERE request_id = ? OR idempotency_key = ?",
                    (request_id, idempotency_key),
                )
            ).fetchone()
        return replace(self._from_row(row), replayed=True) if row is not None else None

    async def commit(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        request_sha256: bytes,
        principal_id: str,
        source_session_id: str,
        source_generation: int,
        action: SessionActionKind | str,
        destination_session_id: str | None,
    ) -> SessionTransitionResult:
        selected = session_action_kind(action)
        if not request_id or not idempotency_key or len(request_sha256) != 32:
            raise SessionActionError("session action identifiers are invalid")
        if selected in {
            SessionActionKind.CREATE,
            SessionActionKind.SWITCH,
            SessionActionKind.RESTART,
        }:
            if not destination_session_id or destination_session_id == source_session_id:
                raise SessionActionError("session action requires a fresh destination")
        elif destination_session_id is not None:
            raise SessionActionError("ending a session cannot include a destination")

        occurred_at = datetime.now(UTC)
        async with aiosqlite.connect(self.path) as database:
            database.row_factory = aiosqlite.Row
            await database.execute("BEGIN IMMEDIATE")
            replay = await (
                await database.execute(
                    "SELECT * FROM session_transitions WHERE request_id = ? OR idempotency_key = ?",
                    (request_id, idempotency_key),
                )
            ).fetchone()
            if replay is not None:
                if bytes(replay["request_sha256"]) != request_sha256:
                    await database.rollback()
                    raise SessionActionConflictError(
                        "session action idempotency key was reused with different parameters"
                    )
                await database.rollback()
                return replace(self._from_row(replay), replayed=True)

            source = await (
                await database.execute(
                    "SELECT generation, status FROM sessions "
                    "WHERE principal_id = ? AND session_id = ?",
                    (principal_id, source_session_id),
                )
            ).fetchone()
            if source is None:
                await database.rollback()
                raise SessionActionError("originating session is unavailable")
            if int(source["generation"]) != source_generation or str(source["status"]) != "active":
                await database.rollback()
                raise SessionActionConflictError(
                    "originating session generation is no longer active"
                )
            if destination_session_id is not None:
                destination = await (
                    await database.execute(
                        "SELECT status FROM sessions WHERE principal_id = ? AND session_id = ?",
                        (principal_id, destination_session_id),
                    )
                ).fetchone()
                if destination is None or str(destination["status"]) != "active":
                    await database.rollback()
                    raise SessionActionError("destination session is unavailable")

            await database.execute(
                """INSERT INTO session_transitions(
                    request_id, idempotency_key, request_sha256, principal_id,
                    source_session_id, source_generation, action,
                    destination_session_id, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request_id,
                    idempotency_key,
                    request_sha256,
                    principal_id,
                    source_session_id,
                    source_generation,
                    selected.value,
                    destination_session_id,
                    occurred_at.isoformat(),
                ),
            )
            await database.execute(
                "UPDATE sessions SET status = 'ended' WHERE principal_id = ? AND session_id = ?",
                (principal_id, source_session_id),
            )
            await database.commit()
        return SessionTransitionResult(
            request_id=request_id,
            idempotency_key=idempotency_key,
            principal_id=principal_id,
            source_session_id=source_session_id,
            source_generation=source_generation,
            action=selected,
            destination_session_id=destination_session_id,
            occurred_at=occurred_at,
        )

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> SessionTransitionResult:
        return SessionTransitionResult(
            request_id=str(row["request_id"]),
            idempotency_key=str(row["idempotency_key"]),
            principal_id=str(row["principal_id"]),
            source_session_id=str(row["source_session_id"]),
            source_generation=int(row["source_generation"]),
            action=session_action_kind(str(row["action"])),
            destination_session_id=(
                str(row["destination_session_id"])
                if row["destination_session_id"] is not None
                else None
            ),
            occurred_at=datetime.fromisoformat(str(row["occurred_at"])).astimezone(UTC),
        )
