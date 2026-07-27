from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from secrets import token_hex
from typing import Literal

import aiosqlite


class JobError(ValueError):
    pass


class JobConflictError(JobError):
    pass


class JobNotFoundError(JobError):
    pass


class JobStateError(JobError):
    pass


class JobLeaseError(JobError):
    pass


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobNotificationPolicy(StrEnum):
    IMMEDIATE = "immediate"
    NEXT_SAFE_PAUSE = "next_safe_pause"
    SILENT = "silent"
    USER_WAITING = "user_waiting"


class JobEventKind(StrEnum):
    ACCEPTED = "accepted"
    STARTED = "started"
    LEASE_RECOVERED = "lease_recovered"
    PROGRESS = "progress"
    WAITING = "waiting"
    FOLLOW_UP_ADDED = "follow_up_added"
    PRIORITY_CHANGED = "priority_changed"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobNotificationDisposition(StrEnum):
    ANNOUNCING = "announcing"
    SPOKEN = "spoken"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    SILENT = "silent"
    VISIBILITY_ONLY = "visibility_only"


TERMINAL_JOB_STATUSES = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_INPUT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
MAX_JOB_INPUTS = 8
MAX_JOB_INPUT_BYTES = 8 * 1024 * 1024
MAX_JOB_INPUT_TOTAL_BYTES = 16 * 1024 * 1024
_EXECUTOR_DETAIL_CODES = {
    JobEventKind.PROGRESS: frozenset(
        {"searching", "downloading", "analyzing", "coding", "testing", "compacting"}
    ),
    JobEventKind.WAITING: frozenset({"waiting_for_user", "waiting_for_resource"}),
    JobEventKind.SUCCEEDED: frozenset({"completed"}),
    JobEventKind.FAILED: frozenset(
        {
            "executor_failed",
            "budget_exhausted",
            "runtime_exhausted",
            "resource_exhausted",
            "step_limit_exhausted",
            "policy_denied",
            "source_unavailable",
            "task_failed",
            "tool_arguments_invalid",
            "model_no_action",
            "model_unavailable",
            "model_rate_limited",
            "model_provider_unavailable",
            "model_request_failed",
            "model_response_invalid",
        }
    ),
    JobEventKind.CANCELLED: frozenset(
        {"cancelled_by_user", "cancelled_by_timeout", "cancelled_by_operator"}
    ),
}


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    principal_id: str
    session_id: str
    generation: int
    notification_policy: JobNotificationPolicy
    priority: int
    status: JobStatus
    budget_microusd: int | None
    max_runtime_seconds: int
    revision: int
    cancel_requested_at: datetime | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    attempt: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class JobEventRecord:
    event_id: str
    delivery_sequence: int
    job_id: str
    revision: int
    kind: JobEventKind
    status: JobStatus
    notification_policy: JobNotificationPolicy
    priority: int
    detail_code: str
    entity_ids: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class JobMutationResult:
    record: JobRecord
    replayed: bool


@dataclass(frozen=True, slots=True)
class JobFollowUp:
    follow_up_id: str
    instruction: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class JobRelatedLink:
    label: str
    locator: str


@dataclass(frozen=True, slots=True)
class JobInput:
    kind: Literal["source", "artifact"]
    name: str
    source_session_id: str
    source_reference: str
    sha256: str
    byte_length: int
    canonical_bytes: bytes = field(repr=False)
    producer_job_id: str | None = None
    entity_id: str | None = None
    artifact_id: str | None = None


@dataclass(frozen=True, slots=True)
class JobClaim:
    record: JobRecord
    instruction: str
    user_message: str
    related_links: tuple[JobRelatedLink, ...]
    inputs: tuple[JobInput, ...]
    follow_ups: tuple[JobFollowUp, ...]
    recovered: bool


@dataclass(frozen=True, slots=True)
class JobHeartbeat:
    record: JobRecord
    follow_ups: tuple[JobFollowUp, ...]


def _default_job_id() -> str:
    return f"job_{token_hex(16)}"


def _utc(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(UTC) if value is not None else None


def _required_utc(value: str) -> datetime:
    parsed = _utc(value)
    assert parsed is not None
    return parsed


def _record(row: aiosqlite.Row) -> JobRecord:
    return JobRecord(
        job_id=str(row["job_id"]),
        principal_id=str(row["principal_id"]),
        session_id=str(row["session_id"]),
        generation=int(row["generation"]),
        notification_policy=JobNotificationPolicy(str(row["notification_policy"])),
        priority=int(row["priority"]),
        status=JobStatus(str(row["status"])),
        budget_microusd=(
            int(row["budget_microusd"]) if row["budget_microusd"] is not None else None
        ),
        max_runtime_seconds=int(row["max_runtime_seconds"]),
        revision=int(row["revision"]),
        cancel_requested_at=_utc(row["cancel_requested_at"]),
        lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
        lease_expires_at=_utc(row["lease_expires_at"]),
        attempt=int(row["attempt"]),
        created_at=_required_utc(str(row["created_at"])),
        updated_at=_required_utc(str(row["updated_at"])),
    )


def _event(row: aiosqlite.Row) -> JobEventRecord:
    return JobEventRecord(
        event_id=str(row["event_id"]),
        delivery_sequence=int(row["delivery_sequence"]),
        job_id=str(row["job_id"]),
        revision=int(row["revision"]),
        kind=JobEventKind(str(row["kind"])),
        status=JobStatus(str(row["status"])),
        notification_policy=JobNotificationPolicy(str(row["notification_policy"])),
        priority=int(row["priority"]),
        detail_code=str(row["detail_code"]),
        entity_ids=tuple(json.loads(str(row["entity_ids_json"]))),
        artifact_ids=tuple(json.loads(str(row["artifact_ids_json"]))),
        occurred_at=_required_utc(str(row["occurred_at"])),
    )


def _follow_up(row: aiosqlite.Row) -> JobFollowUp:
    return JobFollowUp(
        follow_up_id=str(row["follow_up_id"]),
        instruction=str(row["instruction"]),
        created_at=_required_utc(str(row["created_at"])),
    )


def _digest(value: object) -> bytes:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).digest()


class JobStore:
    def __init__(
        self,
        path: Path,
        *,
        id_factory: Callable[[], str] = _default_job_id,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.path = path
        self.id_factory = id_factory
        self.clock = clock

    async def accept(
        self,
        *,
        principal_id: str,
        session_id: str,
        generation: int,
        idempotency_key: str,
        instruction: str,
        user_message: str = "",
        related_links: tuple[JobRelatedLink, ...] = (),
        inputs: tuple[JobInput, ...] = (),
        notification_policy: JobNotificationPolicy = JobNotificationPolicy.NEXT_SAFE_PAUSE,
        priority: int = 2,
        budget_microusd: int | None = None,
        max_runtime_seconds: int = 300,
    ) -> JobMutationResult:
        if not instruction or len(instruction) > 12_000:
            raise JobError("job instruction must contain 1 through 12000 characters")
        self._validate_attempt_context(user_message, related_links)
        if generation <= 0:
            raise JobError("job generation must be positive")
        if priority not in range(5):
            raise JobError("job priority must be between 0 and 4")
        if budget_microusd is not None and budget_microusd <= 0:
            raise JobError("job budget must be positive")
        if not 1 <= max_runtime_seconds <= 86_400:
            raise JobError("job runtime limit must be between 1 and 86400 seconds")
        self._validate_inputs(session_id, inputs)
        request_sha256 = _digest(
            {
                "session_id": session_id,
                "generation": generation,
                "instruction": instruction,
                "user_message": user_message,
                "inputs": [
                    {
                        "kind": item.kind,
                        "name": item.name,
                        "source_session_id": item.source_session_id,
                        "source_reference": item.source_reference,
                        "producer_job_id": item.producer_job_id,
                        "entity_id": item.entity_id,
                        "artifact_id": item.artifact_id,
                        "sha256": item.sha256,
                        "byte_length": item.byte_length,
                    }
                    for item in inputs
                ],
                "notification_policy": notification_policy.value,
                "priority": priority,
                "budget_microusd": budget_microusd,
                "max_runtime_seconds": max_runtime_seconds,
            }
        )
        now = self.clock().astimezone(UTC).isoformat()
        job_id = self.id_factory()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            existing = await (
                await db.execute(
                    "SELECT * FROM background_jobs WHERE principal_id = ? AND idempotency_key = ?",
                    (principal_id, idempotency_key),
                )
            ).fetchone()
            if existing is not None:
                if bytes(existing["request_sha256"]) != request_sha256:
                    await db.rollback()
                    raise JobConflictError("job idempotency key was reused with different input")
                await db.rollback()
                return JobMutationResult(_record(existing), replayed=True)
            await db.execute(
                """INSERT INTO background_jobs(
                    job_id, principal_id, session_id, generation, idempotency_key,
                    request_sha256, instruction, notification_policy, priority, status,
                    budget_microusd, max_runtime_seconds, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, 1, ?, ?)""",
                (
                    job_id,
                    principal_id,
                    session_id,
                    generation,
                    idempotency_key,
                    request_sha256,
                    instruction,
                    notification_policy.value,
                    priority,
                    budget_microusd,
                    max_runtime_seconds,
                    now,
                    now,
                ),
            )
            await db.executemany(
                """INSERT INTO background_job_inputs(
                    job_id, position, kind, name, source_session_id, source_reference,
                    producer_job_id, entity_id, artifact_id, sha256, byte_length,
                    canonical_bytes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    (
                        job_id,
                        position,
                        item.kind,
                        item.name,
                        item.source_session_id,
                        item.source_reference,
                        item.producer_job_id,
                        item.entity_id,
                        item.artifact_id,
                        item.sha256,
                        item.byte_length,
                        item.canonical_bytes,
                    )
                    for position, item in enumerate(inputs)
                ),
            )
            await self._insert_attempt_context(
                db,
                job_id=job_id,
                attempt=1,
                user_message=user_message,
                related_links=related_links,
                created_at=now,
            )
            await self._insert_event(
                db,
                job_id=job_id,
                revision=1,
                kind=JobEventKind.ACCEPTED,
                status=JobStatus.QUEUED,
                notification_policy=notification_policy,
                priority=priority,
                detail_code="accepted",
                occurred_at=now,
            )
            await db.commit()
            row = await self._job_row(db, job_id)
            return JobMutationResult(_record(row), replayed=False)

    async def get(self, *, principal_id: str, job_id: str) -> JobRecord | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    "SELECT * FROM background_jobs WHERE principal_id = ? AND job_id = ?",
                    (principal_id, job_id),
                )
            ).fetchone()
        return _record(row) if row is not None else None

    async def list_for_session(
        self, *, principal_id: str, session_id: str, limit: int = 50
    ) -> tuple[JobRecord, ...]:
        if not 1 <= limit <= 100:
            raise JobError("job list limit must be between 1 and 100")
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """SELECT * FROM background_jobs
                    WHERE principal_id = ? AND session_id = ?
                    ORDER BY created_at DESC, job_id DESC LIMIT ?""",
                    (principal_id, session_id, limit),
                )
            ).fetchall()
        return tuple(_record(row) for row in rows)

    async def events(self, job_id: str, *, after_revision: int = 0) -> tuple[JobEventRecord, ...]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """SELECT * FROM background_job_events
                    WHERE job_id = ? AND revision > ? ORDER BY revision""",
                    (job_id, after_revision),
                )
            ).fetchall()
        return tuple(_event(row) for row in rows)

    async def session_events(
        self,
        session_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[JobEventRecord, ...]:
        if after_sequence < 0:
            raise JobError("job event delivery sequence cannot be negative")
        if not 1 <= limit <= 500:
            raise JobError("job event delivery limit must be between 1 and 500")
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """SELECT events.* FROM background_job_events AS events
                    INNER JOIN background_jobs AS jobs ON jobs.job_id = events.job_id
                    WHERE jobs.session_id = ? AND events.delivery_sequence > ?
                    ORDER BY events.delivery_sequence LIMIT ?""",
                    (session_id, after_sequence, limit),
                )
            ).fetchall()
        return tuple(_event(row) for row in rows)

    async def notification_disposition(
        self,
        *,
        session_id: str,
        event_id: str,
    ) -> JobNotificationDisposition | None:
        async with aiosqlite.connect(self.path) as db:
            row = await (
                await db.execute(
                    """SELECT disposition FROM background_job_notification_deliveries
                    WHERE session_id = ? AND event_id = ?""",
                    (session_id, event_id),
                )
            ).fetchone()
        return JobNotificationDisposition(str(row[0])) if row is not None else None

    async def claim_notification(
        self,
        *,
        session_id: str,
        event_id: str,
        claim_owner: str,
    ) -> bool:
        if not _REFERENCE.fullmatch(claim_owner):
            raise JobError("invalid notification claim owner")
        now = self.clock().astimezone(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            event = await (
                await db.execute(
                    """SELECT 1 FROM background_job_events AS events
                    INNER JOIN background_jobs AS jobs ON jobs.job_id = events.job_id
                    WHERE events.event_id = ? AND jobs.session_id = ?""",
                    (event_id, session_id),
                )
            ).fetchone()
            if event is None:
                await db.rollback()
                raise JobNotFoundError("background job event not found")
            existing = await (
                await db.execute(
                    """SELECT disposition FROM background_job_notification_deliveries
                    WHERE event_id = ?""",
                    (event_id,),
                )
            ).fetchone()
            if existing is not None:
                await db.rollback()
                return False
            await db.execute(
                """INSERT INTO background_job_notification_deliveries(
                    event_id, session_id, claim_owner, disposition, updated_at
                ) VALUES (?, ?, ?, 'announcing', ?)""",
                (event_id, session_id, claim_owner, now),
            )
            await db.commit()
            return True

    async def release_notification(
        self,
        *,
        session_id: str,
        event_id: str,
        claim_owner: str,
    ) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """DELETE FROM background_job_notification_deliveries
                WHERE session_id = ? AND event_id = ? AND claim_owner = ?
                AND disposition = 'announcing'""",
                (session_id, event_id, claim_owner),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def complete_notification(
        self,
        *,
        session_id: str,
        event_id: str,
        claim_owner: str,
        disposition: JobNotificationDisposition,
    ) -> bool:
        if disposition not in {
            JobNotificationDisposition.SPOKEN,
            JobNotificationDisposition.INTERRUPTED,
            JobNotificationDisposition.FAILED,
        }:
            raise JobError("invalid claimed notification disposition")
        now = self.clock().astimezone(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE background_job_notification_deliveries
                SET disposition = ?, updated_at = ?
                WHERE session_id = ? AND event_id = ? AND claim_owner = ?
                AND disposition = 'announcing'""",
                (disposition.value, now, session_id, event_id, claim_owner),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def suppress_notification(
        self,
        *,
        session_id: str,
        event_id: str,
        disposition: JobNotificationDisposition,
    ) -> JobNotificationDisposition:
        if disposition not in {
            JobNotificationDisposition.SILENT,
            JobNotificationDisposition.VISIBILITY_ONLY,
        }:
            raise JobError("invalid suppressed notification disposition")
        now = self.clock().astimezone(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            event = await (
                await db.execute(
                    """SELECT 1 FROM background_job_events AS events
                    INNER JOIN background_jobs AS jobs ON jobs.job_id = events.job_id
                    WHERE events.event_id = ? AND jobs.session_id = ?""",
                    (event_id, session_id),
                )
            ).fetchone()
            if event is None:
                await db.rollback()
                raise JobNotFoundError("background job event not found")
            existing = await (
                await db.execute(
                    """SELECT disposition FROM background_job_notification_deliveries
                    WHERE event_id = ?""",
                    (event_id,),
                )
            ).fetchone()
            if existing is not None:
                await db.rollback()
                return JobNotificationDisposition(str(existing[0]))
            await db.execute(
                """INSERT INTO background_job_notification_deliveries(
                    event_id, session_id, claim_owner, disposition, updated_at
                ) VALUES (?, ?, NULL, ?, ?)""",
                (event_id, session_id, disposition.value, now),
            )
            await db.commit()
            return disposition

    async def add_follow_up(
        self,
        *,
        principal_id: str,
        job_id: str,
        instruction: str,
        idempotency_key: str,
    ) -> JobMutationResult:
        if not instruction or len(instruction) > 4_000:
            raise JobError("job follow-up must contain 1 through 4000 characters")
        return await self._command(
            principal_id=principal_id,
            job_id=job_id,
            idempotency_key=idempotency_key,
            kind="follow_up",
            payload={"instruction": instruction},
            apply=lambda db, row, now: self._apply_follow_up(
                db, row, now, idempotency_key, instruction
            ),
        )

    async def set_priority(
        self,
        *,
        principal_id: str,
        job_id: str,
        priority: int,
        idempotency_key: str,
    ) -> JobMutationResult:
        if priority not in range(5):
            raise JobError("job priority must be between 0 and 4")
        return await self._command(
            principal_id=principal_id,
            job_id=job_id,
            idempotency_key=idempotency_key,
            kind="set_priority",
            payload={"priority": priority},
            apply=lambda db, row, now: self._apply_priority(db, row, now, priority),
        )

    async def request_cancel(
        self,
        *,
        principal_id: str,
        job_id: str,
        idempotency_key: str,
    ) -> JobMutationResult:
        return await self._command(
            principal_id=principal_id,
            job_id=job_id,
            idempotency_key=idempotency_key,
            kind="cancel",
            payload={},
            apply=self._apply_cancel,
        )

    async def retry(
        self,
        *,
        principal_id: str,
        job_id: str,
        idempotency_key: str,
        user_message: str = "",
        related_links: tuple[JobRelatedLink, ...] = (),
    ) -> JobMutationResult:
        self._validate_attempt_context(user_message, related_links)
        return await self._command(
            principal_id=principal_id,
            job_id=job_id,
            idempotency_key=idempotency_key,
            kind="retry",
            payload={"user_message": user_message},
            apply=lambda db, row, now: self._apply_retry(db, row, now, user_message, related_links),
        )

    async def claim_next(self, *, lease_owner: str, lease_seconds: int = 30) -> JobClaim | None:
        if not _REFERENCE.fullmatch(lease_owner):
            raise JobError("invalid job lease owner")
        if not 1 <= lease_seconds <= 300:
            raise JobError("job lease must be between 1 and 300 seconds")
        now_value = self.clock().astimezone(UTC)
        now = now_value.isoformat()
        expires = (now_value + timedelta(seconds=lease_seconds)).isoformat()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    """SELECT * FROM background_jobs
                    WHERE status = 'queued'
                       OR (status = 'running' AND lease_expires_at <= ?)
                    ORDER BY priority, created_at, job_id LIMIT 1""",
                    (now,),
                )
            ).fetchone()
            if row is None:
                await db.rollback()
                return None
            recovered = str(row["status"]) == JobStatus.RUNNING.value
            revision = int(row["revision"]) + 1
            await db.execute(
                """UPDATE background_jobs SET status = 'running', revision = ?,
                lease_owner = ?, lease_expires_at = ?, attempt = attempt + 1, updated_at = ?
                WHERE job_id = ?""",
                (revision, lease_owner, expires, now, row["job_id"]),
            )
            kind = JobEventKind.LEASE_RECOVERED if recovered else JobEventKind.STARTED
            await self._insert_event_from_row(
                db,
                row,
                revision=revision,
                kind=kind,
                status=JobStatus.RUNNING,
                detail_code=kind.value,
                occurred_at=now,
            )
            follow_up_rows = await (
                await db.execute(
                    """SELECT follow_up_id, instruction, created_at
                    FROM background_job_follow_ups
                    WHERE job_id = ? ORDER BY created_at, follow_up_id""",
                    (row["job_id"],),
                )
            ).fetchall()
            attempt = int(row["attempt"]) + 1
            user_message, related_links = await self._attempt_context(
                db, str(row["job_id"]), attempt
            )
            inputs = await self._inputs(db, str(row["job_id"]))
            await db.commit()
            current = await self._job_row(db, str(row["job_id"]))
            return JobClaim(
                record=_record(current),
                instruction=str(row["instruction"]),
                user_message=user_message,
                related_links=related_links,
                inputs=inputs,
                follow_ups=tuple(_follow_up(item) for item in follow_up_rows),
                recovered=recovered,
            )

    async def heartbeat(
        self, *, job_id: str, lease_owner: str, lease_seconds: int = 30
    ) -> JobHeartbeat:
        if not 1 <= lease_seconds <= 300:
            raise JobError("job lease must be between 1 and 300 seconds")
        now_value = self.clock().astimezone(UTC)
        expires = (now_value + timedelta(seconds=lease_seconds)).isoformat()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            row = await self._job_row(db, job_id)
            self._require_lease(row, lease_owner, now_value)
            await db.execute(
                """UPDATE background_jobs SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ?""",
                (expires, now_value.isoformat(), job_id),
            )
            follow_up_rows = await (
                await db.execute(
                    """SELECT follow_up_id, instruction, created_at
                    FROM background_job_follow_ups
                    WHERE job_id = ? ORDER BY created_at, follow_up_id""",
                    (job_id,),
                )
            ).fetchall()
            await db.commit()
            return JobHeartbeat(
                record=_record(await self._job_row(db, job_id)),
                follow_ups=tuple(_follow_up(item) for item in follow_up_rows),
            )

    async def publish(
        self,
        *,
        job_id: str,
        lease_owner: str,
        kind: JobEventKind,
        detail_code: str,
        entity_ids: tuple[str, ...] = (),
        artifact_ids: tuple[str, ...] = (),
    ) -> JobRecord:
        allowed = {
            JobEventKind.PROGRESS,
            JobEventKind.WAITING,
            JobEventKind.SUCCEEDED,
            JobEventKind.FAILED,
            JobEventKind.CANCELLED,
        }
        if kind not in allowed:
            raise JobError("executor cannot publish that job event kind")
        if detail_code not in _EXECUTOR_DETAIL_CODES[kind]:
            raise JobError("unregistered job event detail code")
        if len(entity_ids) > 50 or len(artifact_ids) > 50:
            raise JobError("job event reference limit exceeded")
        if not all(_REFERENCE.fullmatch(item) for item in (*entity_ids, *artifact_ids)):
            raise JobError("invalid job event reference")
        now_value = self.clock().astimezone(UTC)
        now = now_value.isoformat()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            row = await self._job_row(db, job_id)
            self._require_lease(row, lease_owner, now_value)
            cancel_requested = row["cancel_requested_at"] is not None
            if cancel_requested and kind is not JobEventKind.CANCELLED:
                await db.rollback()
                raise JobStateError("cancellation was requested before the executor result")
            status = {
                JobEventKind.PROGRESS: JobStatus.RUNNING,
                JobEventKind.WAITING: JobStatus.WAITING,
                JobEventKind.SUCCEEDED: JobStatus.SUCCEEDED,
                JobEventKind.FAILED: JobStatus.FAILED,
                JobEventKind.CANCELLED: JobStatus.CANCELLED,
            }[kind]
            revision = int(row["revision"]) + 1
            retain_lease = status is JobStatus.RUNNING
            await db.execute(
                """UPDATE background_jobs SET status = ?, revision = ?, updated_at = ?,
                lease_owner = ?, lease_expires_at = ? WHERE job_id = ?""",
                (
                    status.value,
                    revision,
                    now,
                    lease_owner if retain_lease else None,
                    row["lease_expires_at"] if retain_lease else None,
                    job_id,
                ),
            )
            await self._insert_event_from_row(
                db,
                row,
                revision=revision,
                kind=kind,
                status=status,
                detail_code=detail_code,
                occurred_at=now,
                entity_ids=entity_ids,
                artifact_ids=artifact_ids,
            )
            await db.commit()
            return _record(await self._job_row(db, job_id))

    async def _command(
        self,
        *,
        principal_id: str,
        job_id: str,
        idempotency_key: str,
        kind: str,
        payload: object,
        apply: Callable[[aiosqlite.Connection, aiosqlite.Row, str], Awaitable[None]],
    ) -> JobMutationResult:
        request_sha256 = _digest({"kind": kind, "payload": payload})
        now = self.clock().astimezone(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT * FROM background_jobs WHERE principal_id = ? AND job_id = ?",
                    (principal_id, job_id),
                )
            ).fetchone()
            if row is None:
                await db.rollback()
                raise JobNotFoundError("background job not found")
            replay = await (
                await db.execute(
                    """SELECT * FROM background_job_commands
                    WHERE job_id = ? AND idempotency_key = ?""",
                    (job_id, idempotency_key),
                )
            ).fetchone()
            if replay is not None:
                if bytes(replay["request_sha256"]) != request_sha256:
                    await db.rollback()
                    raise JobConflictError("job command key was reused with different input")
                await db.rollback()
                return JobMutationResult(_record(row), replayed=True)
            await apply(db, row, now)
            current = await self._job_row(db, job_id)
            command_id = (
                "job-command:"
                + hashlib.sha256(f"{job_id}:{idempotency_key}".encode()).hexdigest()[:32]
            )
            await db.execute(
                """INSERT INTO background_job_commands(
                    command_id, job_id, idempotency_key, request_sha256,
                    kind, result_revision, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    command_id,
                    job_id,
                    idempotency_key,
                    request_sha256,
                    kind,
                    current["revision"],
                    now,
                ),
            )
            await db.commit()
            return JobMutationResult(_record(current), replayed=False)

    async def _apply_follow_up(
        self,
        db: aiosqlite.Connection,
        row: aiosqlite.Row,
        now: str,
        idempotency_key: str,
        instruction: str,
    ) -> None:
        status = JobStatus(str(row["status"]))
        if status in TERMINAL_JOB_STATUSES:
            raise JobStateError("cannot follow up a terminal background job")
        revision = int(row["revision"]) + 1
        next_status = JobStatus.QUEUED if status is JobStatus.WAITING else status
        follow_up_id = (
            "job-follow-up:"
            + hashlib.sha256(f"{row['job_id']}:{idempotency_key}".encode()).hexdigest()[:32]
        )
        await db.execute(
            """INSERT INTO background_job_follow_ups(
                follow_up_id, job_id, idempotency_key, request_sha256, instruction, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                follow_up_id,
                row["job_id"],
                idempotency_key,
                _digest({"instruction": instruction}),
                instruction,
                now,
            ),
        )
        await db.execute(
            """UPDATE background_jobs SET status = ?, revision = ?, updated_at = ?
            WHERE job_id = ?""",
            (next_status.value, revision, now, row["job_id"]),
        )
        await self._insert_event_from_row(
            db,
            row,
            revision=revision,
            kind=JobEventKind.FOLLOW_UP_ADDED,
            status=next_status,
            detail_code="follow_up_added",
            occurred_at=now,
        )

    async def _apply_priority(
        self, db: aiosqlite.Connection, row: aiosqlite.Row, now: str, priority: int
    ) -> None:
        status = JobStatus(str(row["status"]))
        if status in TERMINAL_JOB_STATUSES:
            raise JobStateError("cannot reprioritize a terminal background job")
        revision = int(row["revision"]) + 1
        await db.execute(
            """UPDATE background_jobs SET priority = ?, revision = ?, updated_at = ?
            WHERE job_id = ?""",
            (priority, revision, now, row["job_id"]),
        )
        await self._insert_event_from_row(
            db,
            row,
            revision=revision,
            kind=JobEventKind.PRIORITY_CHANGED,
            status=status,
            priority=priority,
            detail_code="priority_changed",
            occurred_at=now,
        )

    async def _apply_cancel(self, db: aiosqlite.Connection, row: aiosqlite.Row, now: str) -> None:
        status = JobStatus(str(row["status"]))
        if status in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
            raise JobStateError("cannot cancel a completed background job")
        if status is JobStatus.CANCELLED or row["cancel_requested_at"] is not None:
            return
        revision = int(row["revision"]) + 1
        if status in {JobStatus.QUEUED, JobStatus.WAITING}:
            next_status = JobStatus.CANCELLED
            kind = JobEventKind.CANCELLED
            lease_owner = None
            lease_expires_at = None
        else:
            next_status = status
            kind = JobEventKind.CANCELLATION_REQUESTED
            lease_owner = row["lease_owner"]
            lease_expires_at = row["lease_expires_at"]
        await db.execute(
            """UPDATE background_jobs SET status = ?, revision = ?, cancel_requested_at = ?,
            lease_owner = ?, lease_expires_at = ?, updated_at = ? WHERE job_id = ?""",
            (
                next_status.value,
                revision,
                now,
                lease_owner,
                lease_expires_at,
                now,
                row["job_id"],
            ),
        )
        await self._insert_event_from_row(
            db,
            row,
            revision=revision,
            kind=kind,
            status=next_status,
            detail_code=kind.value,
            occurred_at=now,
        )

    async def _apply_retry(
        self,
        db: aiosqlite.Connection,
        row: aiosqlite.Row,
        now: str,
        user_message: str,
        related_links: tuple[JobRelatedLink, ...],
    ) -> None:
        status = JobStatus(str(row["status"]))
        if status is not JobStatus.FAILED:
            raise JobStateError("only a failed background job can be retried")
        if int(row["attempt"]) >= 3:
            raise JobStateError("background job retry limit reached")
        revision = int(row["revision"]) + 1
        await self._insert_attempt_context(
            db,
            job_id=str(row["job_id"]),
            attempt=int(row["attempt"]) + 1,
            user_message=user_message,
            related_links=related_links,
            created_at=now,
        )
        await db.execute(
            """UPDATE background_jobs SET status = 'queued', revision = ?,
            cancel_requested_at = NULL, lease_owner = NULL, lease_expires_at = NULL,
            updated_at = ? WHERE job_id = ?""",
            (revision, now, row["job_id"]),
        )
        await self._insert_event_from_row(
            db,
            row,
            revision=revision,
            kind=JobEventKind.ACCEPTED,
            status=JobStatus.QUEUED,
            detail_code="retried",
            occurred_at=now,
        )

    @staticmethod
    def _validate_attempt_context(
        user_message: str,
        related_links: tuple[JobRelatedLink, ...],
    ) -> None:
        if len(user_message) > 20_000:
            raise JobError("job user message must contain at most 20000 characters")
        if len(related_links) > 100:
            raise JobError("job related links must contain at most 100 items")
        for item in related_links:
            if not item.label or len(item.label) > 300:
                raise JobError("job related link label must contain 1 through 300 characters")
            if not item.locator or len(item.locator) > 2_048:
                raise JobError("job related link locator must contain 1 through 2048 characters")

    @staticmethod
    def _validate_inputs(session_id: str, inputs: tuple[JobInput, ...]) -> None:
        if len(inputs) > MAX_JOB_INPUTS:
            raise JobError(f"job inputs must contain at most {MAX_JOB_INPUTS} items")
        names: set[str] = set()
        identities: set[tuple[str, str, str | None]] = set()
        total_bytes = 0
        for item in inputs:
            if item.kind not in {"source", "artifact"}:
                raise JobError("job input kind is invalid")
            if not _INPUT_NAME.fullmatch(item.name):
                raise JobError("job input name is invalid")
            folded_name = item.name.casefold()
            if folded_name in names:
                raise JobError("job input names must be unique")
            names.add(folded_name)
            if item.source_session_id != session_id:
                raise JobError("job input belongs to another session")
            if not item.source_reference or len(item.source_reference) > 2_048:
                raise JobError("job input reference must contain 1 through 2048 characters")
            if item.producer_job_id is not None and not _REFERENCE.fullmatch(item.producer_job_id):
                raise JobError("job input producer ID is invalid")
            if item.entity_id is not None and not _REFERENCE.fullmatch(item.entity_id):
                raise JobError("job input entity ID is invalid")
            if item.artifact_id is not None and not _REFERENCE.fullmatch(item.artifact_id):
                raise JobError("job input artifact ID is invalid")
            if item.kind == "source" and any(
                value is not None
                for value in (item.producer_job_id, item.entity_id, item.artifact_id)
            ):
                raise JobError("foreground source input has unexpected producer metadata")
            if item.kind == "artifact" and (
                item.producer_job_id is None or item.artifact_id is None
            ):
                raise JobError("artifact input requires producer and artifact IDs")
            identity = (item.kind, item.source_reference, item.producer_job_id)
            if identity in identities:
                raise JobError("job inputs must not repeat the same source")
            identities.add(identity)
            if not 0 < item.byte_length <= MAX_JOB_INPUT_BYTES:
                raise JobError("job input exceeds the per-file byte limit")
            if len(item.canonical_bytes) != item.byte_length:
                raise JobError("job input byte length does not match its snapshot")
            if not re.fullmatch(r"[a-f0-9]{64}", item.sha256):
                raise JobError("job input hash is invalid")
            if hashlib.sha256(item.canonical_bytes).hexdigest() != item.sha256:
                raise JobError("job input snapshot does not match its hash")
            total_bytes += item.byte_length
        if total_bytes > MAX_JOB_INPUT_TOTAL_BYTES:
            raise JobError("job inputs exceed the aggregate byte limit")

    @staticmethod
    async def _insert_attempt_context(
        db: aiosqlite.Connection,
        *,
        job_id: str,
        attempt: int,
        user_message: str,
        related_links: tuple[JobRelatedLink, ...],
        created_at: str,
    ) -> None:
        await db.execute(
            """INSERT INTO background_job_attempt_contexts(
                job_id, attempt, user_message, related_links_json, created_at
            ) VALUES (?, ?, ?, ?, ?)""",
            (
                job_id,
                attempt,
                user_message,
                json.dumps(
                    [{"label": item.label, "locator": item.locator} for item in related_links],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                created_at,
            ),
        )

    @staticmethod
    async def _attempt_context(
        db: aiosqlite.Connection,
        job_id: str,
        attempt: int,
    ) -> tuple[str, tuple[JobRelatedLink, ...]]:
        row = await (
            await db.execute(
                """SELECT user_message, related_links_json
                FROM background_job_attempt_contexts
                WHERE job_id = ? AND attempt <= ?
                ORDER BY attempt DESC LIMIT 1""",
                (job_id, attempt),
            )
        ).fetchone()
        if row is None:
            return "", ()
        raw_links = json.loads(str(row["related_links_json"]))
        return str(row["user_message"]), tuple(
            JobRelatedLink(label=str(item["label"]), locator=str(item["locator"]))
            for item in raw_links
        )

    @staticmethod
    async def _inputs(
        db: aiosqlite.Connection,
        job_id: str,
    ) -> tuple[JobInput, ...]:
        rows = await (
            await db.execute(
                """SELECT kind, name, source_session_id, source_reference,
                producer_job_id, entity_id, artifact_id, sha256, byte_length,
                canonical_bytes
                FROM background_job_inputs
                WHERE job_id = ? ORDER BY position""",
                (job_id,),
            )
        ).fetchall()
        return tuple(
            JobInput(
                kind=str(row["kind"]),  # type: ignore[arg-type]
                name=str(row["name"]),
                source_session_id=str(row["source_session_id"]),
                source_reference=str(row["source_reference"]),
                producer_job_id=(
                    str(row["producer_job_id"]) if row["producer_job_id"] is not None else None
                ),
                entity_id=str(row["entity_id"]) if row["entity_id"] is not None else None,
                artifact_id=(str(row["artifact_id"]) if row["artifact_id"] is not None else None),
                sha256=str(row["sha256"]),
                byte_length=int(row["byte_length"]),
                canonical_bytes=bytes(row["canonical_bytes"]),
            )
            for row in rows
        )

    @staticmethod
    async def _job_row(db: aiosqlite.Connection, job_id: str) -> aiosqlite.Row:
        row = await (
            await db.execute("SELECT * FROM background_jobs WHERE job_id = ?", (job_id,))
        ).fetchone()
        if row is None:
            raise JobNotFoundError("background job not found")
        return row

    @staticmethod
    def _require_lease(row: aiosqlite.Row, lease_owner: str, now: datetime) -> None:
        if str(row["status"]) != JobStatus.RUNNING.value:
            raise JobLeaseError("background job is not running")
        if row["lease_owner"] != lease_owner:
            raise JobLeaseError("background job lease is owned by another executor")
        expires = _utc(row["lease_expires_at"])
        if expires is None or expires <= now:
            raise JobLeaseError("background job lease expired")

    async def _insert_event_from_row(
        self,
        db: aiosqlite.Connection,
        row: aiosqlite.Row,
        *,
        revision: int,
        kind: JobEventKind,
        status: JobStatus,
        detail_code: str,
        occurred_at: str,
        priority: int | None = None,
        entity_ids: tuple[str, ...] = (),
        artifact_ids: tuple[str, ...] = (),
    ) -> None:
        await self._insert_event(
            db,
            job_id=str(row["job_id"]),
            revision=revision,
            kind=kind,
            status=status,
            notification_policy=JobNotificationPolicy(str(row["notification_policy"])),
            priority=int(row["priority"]) if priority is None else priority,
            detail_code=detail_code,
            occurred_at=occurred_at,
            entity_ids=entity_ids,
            artifact_ids=artifact_ids,
        )

    @staticmethod
    async def _insert_event(
        db: aiosqlite.Connection,
        *,
        job_id: str,
        revision: int,
        kind: JobEventKind,
        status: JobStatus,
        notification_policy: JobNotificationPolicy,
        priority: int,
        detail_code: str,
        occurred_at: str,
        entity_ids: tuple[str, ...] = (),
        artifact_ids: tuple[str, ...] = (),
    ) -> None:
        sequence_row = await (
            await db.execute(
                """SELECT COALESCE(MAX(delivery_sequence), 0) + 1
                FROM background_job_events"""
            )
        ).fetchone()
        assert sequence_row is not None
        delivery_sequence = int(sequence_row[0])
        await db.execute(
            """INSERT INTO background_job_events(
                event_id, job_id, revision, kind, status, notification_policy,
                priority, detail_code, entity_ids_json, artifact_ids_json, occurred_at,
                delivery_sequence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"job-event:{job_id}:{revision}",
                job_id,
                revision,
                kind.value,
                status.value,
                notification_policy.value,
                priority,
                detail_code,
                json.dumps(entity_ids, separators=(",", ":")),
                json.dumps(artifact_ids, separators=(",", ":")),
                occurred_at,
                delivery_sequence,
            ),
        )
