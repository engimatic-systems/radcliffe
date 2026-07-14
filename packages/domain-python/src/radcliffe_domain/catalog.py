from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex

import aiosqlite


class InvalidPageCursorError(ValueError):
    pass


class SessionConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    principal_id: str
    created_at: datetime
    last_activity_at: datetime
    title: str | None
    status: str
    protocol_version: int
    generation: int
    worker_mode: str
    worker_status: str


@dataclass(frozen=True, slots=True)
class SessionPage:
    items: tuple[SessionRecord, ...]
    next_cursor: str | None


def _default_session_id() -> str:
    return f"ses_{token_hex(16)}"


def _row_to_record(row: aiosqlite.Row) -> SessionRecord:
    return SessionRecord(
        session_id=str(row["session_id"]),
        principal_id=str(row["principal_id"]),
        created_at=datetime.fromisoformat(str(row["created_at"])).astimezone(UTC),
        last_activity_at=datetime.fromisoformat(str(row["last_activity_at"])).astimezone(UTC),
        title=str(row["title"]) if row["title"] is not None else None,
        status=str(row["status"]),
        protocol_version=int(row["protocol_version"]),
        generation=int(row["generation"]),
        worker_mode=str(row["worker_mode"]),
        worker_status=str(row["worker_status"]),
    )


def _encode_cursor(record: SessionRecord) -> str:
    raw = json.dumps(
        [record.last_activity_at.isoformat(), record.session_id],
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode())
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not all(isinstance(item, str) and item for item in value)
        ):
            raise ValueError
        datetime.fromisoformat(value[0])
        return value[0], value[1]
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidPageCursorError("invalid session page cursor") from exc


class SessionCatalog:
    def __init__(
        self,
        path: Path,
        *,
        id_factory: Callable[[], str] = _default_session_id,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.path = path
        self.id_factory = id_factory
        self.clock = clock

    async def create(
        self,
        *,
        principal_id: str,
        session_id: str | None = None,
        idempotency_key: str | None = None,
        title: str | None = None,
        protocol_version: int = 2,
    ) -> tuple[SessionRecord, bool]:
        now = self.clock().astimezone(UTC).isoformat()
        generated = session_id is None
        worker_mode = "managed" if generated else "external"
        attempts = 4 if generated else 1
        for _attempt in range(attempts):
            candidate = session_id or self.id_factory()
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("BEGIN IMMEDIATE")
                if idempotency_key:
                    existing = await (
                        await db.execute(
                            """SELECT * FROM sessions
                            WHERE principal_id = ? AND idempotency_key = ?""",
                            (principal_id, idempotency_key),
                        )
                    ).fetchone()
                    if existing is not None:
                        await db.rollback()
                        return _row_to_record(existing), False
                existing = await (
                    await db.execute("SELECT * FROM sessions WHERE session_id = ?", (candidate,))
                ).fetchone()
                if existing is not None:
                    await db.rollback()
                    if str(existing["principal_id"]) != principal_id:
                        if generated:
                            continue
                        raise SessionConflictError("session ID is unavailable")
                    return _row_to_record(existing), False
                try:
                    await db.execute(
                        """INSERT INTO sessions(
                            session_id, principal_id, idempotency_key, created_at,
                            last_activity_at, title, status, protocol_version, generation,
                            worker_mode, worker_status, worker_updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, 1, ?, 'stopped', ?)""",
                        (
                            candidate,
                            principal_id,
                            idempotency_key,
                            now,
                            now,
                            title,
                            protocol_version,
                            worker_mode,
                            now,
                        ),
                    )
                    await db.commit()
                except aiosqlite.IntegrityError:
                    await db.rollback()
                    if generated:
                        continue
                    raise
                record = await self.get(principal_id=principal_id, session_id=candidate)
                assert record is not None
                return record, True
        raise SessionConflictError("could not allocate a unique session ID")

    async def get(self, *, principal_id: str, session_id: str) -> SessionRecord | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    "SELECT * FROM sessions WHERE principal_id = ? AND session_id = ?",
                    (principal_id, session_id),
                )
            ).fetchone()
        return _row_to_record(row) if row is not None else None

    async def list(
        self,
        *,
        principal_id: str,
        limit: int = 20,
        cursor: str | None = None,
        include_archived: bool = False,
    ) -> SessionPage:
        if not 1 <= limit <= 100:
            raise ValueError("session page limit must be between 1 and 100")
        parameters: list[str | int] = [principal_id]
        visibility = "" if include_archived else "AND status != 'archived'"
        boundary = ""
        if cursor:
            activity, session_id = _decode_cursor(cursor)
            boundary = "AND (last_activity_at < ? OR (last_activity_at = ? AND session_id < ?))"
            parameters.extend((activity, activity, session_id))
        parameters.append(limit + 1)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    f"""SELECT * FROM sessions WHERE principal_id = ? {visibility} {boundary}
                    ORDER BY last_activity_at DESC, session_id DESC LIMIT ?""",  # noqa: S608
                    parameters,
                )
            ).fetchall()
        records = tuple(_row_to_record(row) for row in rows)
        items = records[:limit]
        return SessionPage(
            items=items,
            next_cursor=_encode_cursor(items[-1]) if len(records) > limit else None,
        )

    async def update(
        self,
        *,
        principal_id: str,
        session_id: str,
        title: str | None,
        status: str,
    ) -> SessionRecord | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE sessions SET title = ?, status = ?
                WHERE principal_id = ? AND session_id = ?""",
                (title, status, principal_id, session_id),
            )
            await db.commit()
        if cursor.rowcount == 0:
            return None
        return await self.get(principal_id=principal_id, session_id=session_id)

    async def touch(
        self,
        *,
        principal_id: str,
        session_id: str,
        occurred_at: datetime | None = None,
    ) -> SessionRecord | None:
        timestamp = (occurred_at or self.clock()).astimezone(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE sessions SET last_activity_at = MAX(last_activity_at, ?)
                WHERE principal_id = ? AND session_id = ?""",
                (timestamp, principal_id, session_id),
            )
            await db.commit()
        if cursor.rowcount == 0:
            return None
        return await self.get(principal_id=principal_id, session_id=session_id)

    async def set_worker_status(
        self,
        *,
        principal_id: str,
        session_id: str,
        worker_status: str,
    ) -> SessionRecord | None:
        timestamp = self.clock().astimezone(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE sessions SET worker_status = ?, worker_updated_at = ?
                WHERE principal_id = ? AND session_id = ?""",
                (worker_status, timestamp, principal_id, session_id),
            )
            await db.commit()
        if cursor.rowcount == 0:
            return None
        return await self.get(principal_id=principal_id, session_id=session_id)

    async def managed_active_since(
        self,
        *,
        principal_id: str,
        active_since: datetime,
    ) -> tuple[SessionRecord, ...]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """SELECT * FROM sessions
                    WHERE principal_id = ? AND worker_mode = 'managed'
                    AND status = 'active' AND last_activity_at >= ?
                    ORDER BY last_activity_at DESC, session_id DESC""",
                    (principal_id, active_since.astimezone(UTC).isoformat()),
                )
            ).fetchall()
        return tuple(_row_to_record(row) for row in rows)
