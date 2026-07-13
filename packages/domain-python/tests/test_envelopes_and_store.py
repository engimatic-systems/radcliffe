from datetime import UTC, datetime

import pytest
from radcliffe_domain.envelope import MessageEnvelope, Sensitivity, SourceKind
from radcliffe_domain.projection import context_projection, project_replay, render_projection
from radcliffe_domain.store import EventStore, StoreConflictError


@pytest.mark.asyncio
async def test_append_replay_is_ordered_idempotent_and_hash_checked(tmp_path) -> None:
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    first = MessageEnvelope.text(
        session_id="s1",
        sequence=1,
        source=SourceKind.USER,
        text="Hello",
        message_id="m1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    second = MessageEnvelope.text(
        session_id="s1",
        sequence=2,
        source=SourceKind.MODEL,
        text="Hello back. Another sentence!",
        message_id="m2",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert await store.append(first)
    assert not await store.append(first)
    await store.append(second)
    assert await store.replay("s1") == (first, second)

    out_of_order = MessageEnvelope.text(
        session_id="s1", sequence=4, source=SourceKind.USER, text="skip"
    )
    with pytest.raises(StoreConflictError, match="expected sequence 3"):
        await store.append(out_of_order)


def test_projection_is_deterministic_source_aware_and_secret_safe() -> None:
    source = MessageEnvelope.text(
        session_id="s1",
        sequence=1,
        source=SourceKind.TEXT_SOURCE,
        text="First sentence. Second sentence!",
        metadata={"format": "markdown"},
        message_id="source-1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    secret = MessageEnvelope.text(
        session_id="s1",
        sequence=2,
        source=SourceKind.SYSTEM,
        text="provider-key",
        sensitivity=Sensitivity.SECRET,
        message_id="secret-1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    first = project_replay((source, secret))
    second = project_replay((source, secret))

    assert first == second
    segments = render_projection(source).segments
    assert [segment.canonical_text.strip() for segment in segments] == [
        "First sentence.",
        "Second sentence!",
    ]
    assert "".join(segment.canonical_text for segment in segments) == source.content.inline_text
    packet = context_projection(secret)
    assert not packet.include
    assert "provider-key" not in packet.content
