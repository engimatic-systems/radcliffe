from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite


class DeviceEnrollmentConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    device_id: str
    owner_id: str
    installation_id: str
    participant_identity: str
    display_name: str
    credential_sha256: bytes
    created_at: datetime
    last_authenticated_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class DeviceAuthAudit:
    event_id: str
    owner_id: str
    device_id: str | None
    event_type: str
    reason: str
    occurred_at: datetime


def credential_digest(credential: str) -> bytes:
    return hashlib.sha256(credential.encode()).digest()


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _record(row: aiosqlite.Row) -> DeviceRecord:
    return DeviceRecord(
        device_id=str(row["device_id"]),
        owner_id=str(row["owner_id"]),
        installation_id=str(row["installation_id"]),
        participant_identity=str(row["participant_identity"]),
        display_name=str(row["display_name"]),
        credential_sha256=bytes(row["credential_sha256"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        last_authenticated_at=_datetime(row["last_authenticated_at"]),
        revoked_at=_datetime(row["revoked_at"]),
    )


class DeviceRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def enroll(
        self,
        *,
        owner_id: str,
        installation_id: str,
        display_name: str,
        credential_sha256: bytes,
    ) -> tuple[DeviceRecord, bool]:
        now = datetime.now(UTC)
        async with aiosqlite.connect(self.path) as database:
            database.row_factory = aiosqlite.Row
            await database.execute("BEGIN IMMEDIATE")
            existing = await (
                await database.execute(
                    "SELECT * FROM devices WHERE owner_id = ? AND installation_id = ?",
                    (owner_id, installation_id),
                )
            ).fetchone()
            if existing is not None:
                record = _record(existing)
                if record.credential_sha256 != credential_sha256 or record.revoked_at is not None:
                    await database.rollback()
                    raise DeviceEnrollmentConflictError(
                        "installation is already enrolled with another credential"
                    )
                await database.rollback()
                return record, False
            device_id = f"dev_{uuid4().hex}"
            participant_identity = f"radcliffe-{device_id}"
            try:
                await database.execute(
                    """INSERT INTO devices(
                        device_id, owner_id, installation_id, participant_identity,
                        display_name, credential_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        device_id,
                        owner_id,
                        installation_id,
                        participant_identity,
                        display_name,
                        credential_sha256,
                        now.isoformat(),
                    ),
                )
            except aiosqlite.IntegrityError as error:
                await database.rollback()
                raise DeviceEnrollmentConflictError(
                    "credential is already enrolled to another installation"
                ) from error
            await database.commit()
        created_record = await self.get(device_id)
        assert created_record is not None
        return created_record, True

    async def authenticate(self, credential_sha256: bytes) -> DeviceRecord | None:
        now = datetime.now(UTC)
        async with aiosqlite.connect(self.path) as database:
            database.row_factory = aiosqlite.Row
            row = await (
                await database.execute(
                    "SELECT * FROM devices WHERE credential_sha256 = ?",
                    (credential_sha256,),
                )
            ).fetchone()
            if row is None:
                return None
            record = _record(row)
            if record.revoked_at is not None:
                return record
            await database.execute(
                "UPDATE devices SET last_authenticated_at = ? WHERE device_id = ?",
                (now.isoformat(), record.device_id),
            )
            await database.commit()
        return replace(record, last_authenticated_at=now)

    async def get(self, device_id: str) -> DeviceRecord | None:
        async with aiosqlite.connect(self.path) as database:
            database.row_factory = aiosqlite.Row
            row = await (
                await database.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,))
            ).fetchone()
        return _record(row) if row is not None else None

    async def revoke(self, *, owner_id: str, device_id: str) -> DeviceRecord | None:
        now = datetime.now(UTC)
        async with aiosqlite.connect(self.path) as database:
            cursor = await database.execute(
                """UPDATE devices SET revoked_at = COALESCE(revoked_at, ?)
                WHERE owner_id = ? AND device_id = ?""",
                (now.isoformat(), owner_id, device_id),
            )
            await database.commit()
            if cursor.rowcount == 0:
                return None
        return await self.get(device_id)

    async def audit(
        self,
        *,
        owner_id: str,
        device_id: str | None,
        event_type: str,
        reason: str,
    ) -> None:
        async with aiosqlite.connect(self.path) as database:
            await database.execute(
                "INSERT INTO device_auth_audit VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid4()),
                    owner_id,
                    device_id,
                    event_type,
                    reason,
                    datetime.now(UTC).isoformat(),
                ),
            )
            await database.commit()

    async def audit_events(self, *, owner_id: str) -> tuple[DeviceAuthAudit, ...]:
        async with aiosqlite.connect(self.path) as database:
            rows = await (
                await database.execute(
                    """SELECT event_id, owner_id, device_id, event_type, reason, occurred_at
                    FROM device_auth_audit WHERE owner_id = ?
                    ORDER BY occurred_at, event_id""",
                    (owner_id,),
                )
            ).fetchall()
        return tuple(
            DeviceAuthAudit(
                event_id=str(row[0]),
                owner_id=str(row[1]),
                device_id=str(row[2]) if row[2] is not None else None,
                event_type=str(row[3]),
                reason=str(row[4]),
                occurred_at=datetime.fromisoformat(str(row[5])),
            )
            for row in rows
        )
