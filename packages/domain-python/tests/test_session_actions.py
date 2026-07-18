from __future__ import annotations

import asyncio

import pytest
from radcliffe_domain.catalog import SessionCatalog
from radcliffe_domain.session_actions import (
    SessionActionConflictError,
    SessionResolutionStatus,
    SessionTransitionStore,
    resolve_session_reference,
)
from radcliffe_domain.store import EventStore


async def catalog(tmp_path) -> SessionCatalog:
    path = tmp_path / "events.sqlite3"
    await EventStore(path).migrate()
    return SessionCatalog(path)


@pytest.mark.asyncio
async def test_catalog_resolution_is_owner_scoped_bounded_and_never_guesses(tmp_path) -> None:
    selected = await catalog(tmp_path)
    await selected.create(principal_id="owner", session_id="current", title="Current")
    first, _ = await selected.create(
        principal_id="owner",
        session_id="first",
        title="Project notes",
    )
    await selected.create(
        principal_id="owner",
        session_id="second",
        title="Project notes",
    )
    unique, _ = await selected.create(
        principal_id="owner",
        session_id="unique",
        title="Travel plan",
    )
    hidden, _ = await selected.create(
        principal_id="another-owner",
        session_id="hidden",
        title="Travel plan",
    )
    await selected.update(
        principal_id="another-owner",
        session_id=hidden.session_id,
        title=hidden.title,
        status="active",
    )

    ambiguous = await resolve_session_reference(
        selected,
        principal_id="owner",
        current_session_id="current",
        reference="project notes",
        choice_limit=2,
    )
    resolved = await resolve_session_reference(
        selected,
        principal_id="owner",
        current_session_id="current",
        reference="travel",
    )
    exact = await resolve_session_reference(
        selected,
        principal_id="owner",
        current_session_id="current",
        reference=first.session_id,
    )
    missing = await resolve_session_reference(
        selected,
        principal_id="owner",
        current_session_id="current",
        reference="hidden",
    )

    assert ambiguous.status is SessionResolutionStatus.AMBIGUOUS
    assert len(ambiguous.choices) == 2
    assert {choice.session_id for choice in ambiguous.choices} == {"first", "second"}
    assert resolved.destination == unique
    assert exact.destination == first
    assert missing.status is SessionResolutionStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_transition_commit_is_atomic_replayable_and_ends_only_the_source(tmp_path) -> None:
    selected = await catalog(tmp_path)
    source, _ = await selected.create(principal_id="owner", session_id="source")
    destination, _ = await selected.create(principal_id="owner", session_id="destination")
    transitions = SessionTransitionStore(selected.path)

    async def commit():
        return await transitions.commit(
            request_id="session:request-1",
            idempotency_key="session-key-1",
            request_sha256=b"a" * 32,
            principal_id="owner",
            source_session_id=source.session_id,
            source_generation=source.generation,
            action="switch",
            destination_session_id=destination.session_id,
        )

    first, replay = await asyncio.gather(commit(), commit())
    refreshed_source = await selected.get(principal_id="owner", session_id=source.session_id)
    refreshed_destination = await selected.get(
        principal_id="owner",
        session_id=destination.session_id,
    )

    assert {first.replayed, replay.replayed} == {False, True}
    assert refreshed_source is not None and refreshed_source.status == "ended"
    assert refreshed_destination is not None and refreshed_destination.status == "active"
    with pytest.raises(SessionActionConflictError, match="different parameters"):
        await transitions.replay(
            request_id="session:request-1",
            idempotency_key="session-key-1",
            request_sha256=b"b" * 32,
        )


@pytest.mark.asyncio
async def test_stale_generation_cannot_commit_a_second_transition(tmp_path) -> None:
    selected = await catalog(tmp_path)
    source, _ = await selected.create(principal_id="owner", session_id="source")
    transitions = SessionTransitionStore(selected.path)
    await transitions.commit(
        request_id="session:end-1",
        idempotency_key="end-key-1",
        request_sha256=b"a" * 32,
        principal_id="owner",
        source_session_id=source.session_id,
        source_generation=source.generation,
        action="end",
        destination_session_id=None,
    )

    with pytest.raises(SessionActionConflictError, match="no longer active"):
        await transitions.commit(
            request_id="session:end-2",
            idempotency_key="end-key-2",
            request_sha256=b"b" * 32,
            principal_id="owner",
            source_session_id=source.session_id,
            source_generation=source.generation,
            action="end",
            destination_session_id=None,
        )
