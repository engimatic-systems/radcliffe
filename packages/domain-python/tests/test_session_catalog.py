import asyncio
from datetime import UTC, datetime, timedelta
from importlib.resources import files

import aiosqlite
import pytest
from radcliffe_domain.catalog import (
    InvalidPageCursorError,
    SessionCatalog,
    SessionConflictError,
)
from radcliffe_domain.envelope import MessageEnvelope, SourceKind
from radcliffe_domain.store import EventStore


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 14, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int = 1) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.mark.asyncio
async def test_generated_session_is_idempotent_and_durable(tmp_path) -> None:
    path = tmp_path / "catalog.sqlite3"
    store = EventStore(path)
    await store.migrate()
    ids = iter(("ses_first", "ses_unused"))
    clock = Clock()
    catalog = SessionCatalog(path, id_factory=lambda: next(ids), clock=clock)

    first, created = await catalog.create(
        principal_id="owner-a",
        idempotency_key="create-1",
        title="Morning notes",
    )
    replay, replay_created = await catalog.create(
        principal_id="owner-a",
        idempotency_key="create-1",
        title="Ignored retry title",
    )

    assert created
    assert not replay_created
    assert replay == first
    assert first.session_id == "ses_first"
    assert first.title == "Morning notes"
    restarted = SessionCatalog(path)
    assert await restarted.get(principal_id="owner-a", session_id="ses_first") == first
    assert await restarted.get(principal_id="owner-b", session_id="ses_first") is None


@pytest.mark.asyncio
async def test_catalog_orders_paginates_and_scopes_with_stable_ties(tmp_path) -> None:
    path = tmp_path / "catalog.sqlite3"
    await EventStore(path).migrate()
    clock = Clock()
    catalog = SessionCatalog(path, clock=clock)
    for session_id in ("session-a", "session-b", "session-c"):
        await catalog.create(principal_id="owner-a", session_id=session_id)
    await catalog.create(principal_id="owner-b", session_id="private-session")
    clock.advance()
    await catalog.touch(principal_id="owner-a", session_id="session-a")

    first_page = await catalog.list(principal_id="owner-a", limit=2)
    second_page = await catalog.list(principal_id="owner-a", limit=2, cursor=first_page.next_cursor)

    assert [item.session_id for item in first_page.items] == ["session-a", "session-c"]
    assert first_page.next_cursor is not None
    assert [item.session_id for item in second_page.items] == ["session-b"]
    assert second_page.next_cursor is None
    assert "private-session" not in {
        item.session_id for item in first_page.items + second_page.items
    }
    with pytest.raises(InvalidPageCursorError):
        await catalog.list(principal_id="owner-a", cursor="not-a-cursor")


@pytest.mark.asyncio
async def test_concurrent_idempotent_creation_allocates_one_record(tmp_path) -> None:
    path = tmp_path / "catalog.sqlite3"
    await EventStore(path).migrate()
    next_number = 0

    def allocate() -> str:
        nonlocal next_number
        next_number += 1
        return f"ses_{next_number}"

    catalog = SessionCatalog(path, id_factory=allocate)
    results = await asyncio.gather(
        *(catalog.create(principal_id="owner", idempotency_key="same-request") for _ in range(12))
    )

    allocated_ids = {record.session_id for record, _created in results}
    assert len(allocated_ids) == 1
    assert next(iter(allocated_ids)).startswith("ses_")
    assert sum(created for _record, created in results) == 1
    assert len((await catalog.list(principal_id="owner")).items) == 1


@pytest.mark.asyncio
async def test_explicit_compatibility_id_cannot_cross_principals(tmp_path) -> None:
    path = tmp_path / "catalog.sqlite3"
    await EventStore(path).migrate()
    catalog = SessionCatalog(path)
    await catalog.create(principal_id="owner-a", session_id="legacy-session")

    with pytest.raises(SessionConflictError, match="unavailable"):
        await catalog.create(principal_id="owner-b", session_id="legacy-session")


@pytest.mark.asyncio
async def test_envelope_append_advances_catalog_activity_via_database_trigger(tmp_path) -> None:
    path = tmp_path / "catalog.sqlite3"
    store = EventStore(path)
    await store.migrate()
    clock = Clock()
    catalog = SessionCatalog(path, clock=clock)
    session, _created = await catalog.create(principal_id="owner", session_id="session-1")
    message_time = session.created_at + timedelta(minutes=5)

    await store.append(
        MessageEnvelope.text(
            session_id=session.session_id,
            sequence=1,
            source=SourceKind.USER,
            text="Message content is not part of the catalog.",
            created_at=message_time,
        )
    )

    updated = await catalog.get(principal_id="owner", session_id=session.session_id)
    assert updated is not None
    assert updated.last_activity_at == message_time
    assert not hasattr(updated, "content")


@pytest.mark.asyncio
async def test_existing_version_one_database_migrates_without_data_loss(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    migration = files("radcliffe_domain.migrations").joinpath("001_initial.sql").read_text()
    async with aiosqlite.connect(path) as db:
        await db.executescript(migration)
        await db.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
            (datetime.now(UTC).isoformat(),),
        )
        await db.commit()

    await EventStore(path).migrate()
    catalog = SessionCatalog(path)
    record, created = await catalog.create(principal_id="owner", session_id="after-upgrade")

    assert created
    assert record.session_id == "after-upgrade"
    async with aiosqlite.connect(path) as db:
        versions = await (await db.execute("SELECT version FROM schema_migrations")).fetchall()
        assert versions == [
            (1,),
            (2,),
            (3,),
            (4,),
            (5,),
            (6,),
            (7,),
            (8,),
            (9,),
            (10,),
            (11,),
            (12,),
            (13,),
            (14,),
        ]
