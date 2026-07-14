from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

import aiosqlite
from radcliffe_protocol.radcliffe.v2.protocol_pb2 import MessageEnvelope as ProtoEnvelope

from radcliffe_domain.envelope import MessageEnvelope


class StoreConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ToolAudit:
    call_id: str
    idempotency_key: str
    session_id: str
    generation: int
    tool_name: str
    arguments_json: bytes
    grant_id: str
    authorization: str
    outcome: str
    resolved_path: str | None = None
    content_sha256: bytes | None = None
    result_json: bytes | None = None


class EventStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )"""
            )
            migrations = files("radcliffe_domain.migrations")
            for resource in sorted(migrations.iterdir(), key=lambda item: item.name):
                if not resource.name.endswith(".sql"):
                    continue
                version_text = resource.name.partition("_")[0]
                if not version_text.isdigit():
                    continue
                version = int(version_text)
                applied = await (
                    await db.execute(
                        "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
                    )
                ).fetchone()
                if applied is not None:
                    continue
                await db.executescript(resource.read_text())
                await db.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(UTC).isoformat()),
                )
                await db.commit()

    async def append(self, envelope: MessageEnvelope) -> bool:
        wire = envelope.to_proto().SerializeToString(deterministic=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            existing = await (
                await db.execute(
                    "SELECT envelope FROM envelopes WHERE message_id = ?",
                    (envelope.message_id,),
                )
            ).fetchone()
            if existing is not None:
                if existing[0] != wire:
                    raise StoreConflictError("message ID was reused with different content")
                await db.rollback()
                return False
            row = await (
                await db.execute(
                    "SELECT COALESCE(MAX(sequence), 0) FROM envelopes WHERE session_id = ?",
                    (envelope.session_id,),
                )
            ).fetchone()
            expected = int(row[0]) + 1 if row is not None else 1
            if envelope.sequence != expected:
                raise StoreConflictError(
                    f"expected sequence {expected}, received {envelope.sequence}"
                )
            await db.execute(
                """INSERT INTO envelopes(
                    session_id, sequence, message_id, content_sha256, envelope, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    envelope.session_id,
                    envelope.sequence,
                    envelope.message_id,
                    envelope.content_sha256,
                    wire,
                    envelope.created_at.isoformat(),
                ),
            )
            await db.commit()
            return True

    async def next_sequence(self, session_id: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            row = await (
                await db.execute(
                    "SELECT COALESCE(MAX(sequence), 0) FROM envelopes WHERE session_id = ?",
                    (session_id,),
                )
            ).fetchone()
        return int(row[0]) + 1 if row is not None else 1

    async def replay(self, session_id: str, after: int = 0) -> tuple[MessageEnvelope, ...]:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """SELECT envelope FROM envelopes
                WHERE session_id = ? AND sequence > ? ORDER BY sequence""",
                (session_id, after),
            )
            rows = await cursor.fetchall()
        return tuple(MessageEnvelope.from_proto(ProtoEnvelope.FromString(row[0])) for row in rows)

    async def record_playback(
        self,
        *,
        acknowledgement_id: str,
        session_id: str,
        generation: int,
        operation_id: str,
        segment_id: str,
        status: str,
        position_ms: int,
        boundary_index: int,
        occurred_at: datetime,
        detail: str = "",
    ) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO playback_acknowledgements VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )""",
                (
                    acknowledgement_id,
                    session_id,
                    generation,
                    operation_id,
                    segment_id,
                    status,
                    position_ms,
                    boundary_index,
                    occurred_at.astimezone(UTC).isoformat(),
                    detail,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def playback_boundary(self, session_id: str, segment_id: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            row = await (
                await db.execute(
                    """SELECT boundary_index FROM playback_acknowledgements
                    WHERE session_id = ? AND segment_id = ?
                    AND status IN ('position', 'completed', 'flushed')
                    ORDER BY occurred_at DESC LIMIT 1""",
                    (session_id, segment_id),
                )
            ).fetchone()
        return int(row[0]) if row is not None else 0

    async def create_tool_audit(self, audit: ToolAudit) -> ToolAudit | None:
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            try:
                await db.execute(
                    """INSERT INTO tool_calls VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )""",
                    (
                        audit.call_id,
                        audit.idempotency_key,
                        audit.session_id,
                        audit.generation,
                        audit.tool_name,
                        audit.arguments_json,
                        audit.grant_id,
                        audit.authorization,
                        audit.outcome,
                        audit.resolved_path,
                        audit.content_sha256,
                        audit.result_json,
                        now,
                        now,
                    ),
                )
                await db.commit()
                return None
            except aiosqlite.IntegrityError:
                row = await (
                    await db.execute(
                        "SELECT * FROM tool_calls WHERE call_id = ? OR idempotency_key = ?",
                        (audit.call_id, audit.idempotency_key),
                    )
                ).fetchone()
                if row is None:
                    raise
                return ToolAudit(
                    call_id=row[0],
                    idempotency_key=row[1],
                    session_id=row[2],
                    generation=row[3],
                    tool_name=row[4],
                    arguments_json=row[5],
                    grant_id=row[6],
                    authorization=row[7],
                    outcome=row[8],
                    resolved_path=row[9],
                    content_sha256=row[10],
                    result_json=row[11],
                )

    async def finish_tool_audit(self, audit: ToolAudit) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE tool_calls SET authorization = ?, outcome = ?, resolved_path = ?,
                content_sha256 = ?, result_json = ?, updated_at = ? WHERE call_id = ?""",
                (
                    audit.authorization,
                    audit.outcome,
                    audit.resolved_path,
                    audit.content_sha256,
                    audit.result_json,
                    datetime.now(UTC).isoformat(),
                    audit.call_id,
                ),
            )
            await db.commit()
