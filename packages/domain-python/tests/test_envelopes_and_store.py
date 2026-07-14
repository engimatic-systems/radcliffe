from datetime import UTC, datetime

import pytest
from radcliffe_domain.envelope import MessageEnvelope, Sensitivity, SourceKind
from radcliffe_domain.projection import (
    TURN_DISPOSITION_METADATA,
    TURN_EVENT_METADATA,
    TURN_ID_METADATA,
    context_projection,
    context_replay,
    project_replay,
    render_projection,
)
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


def test_context_replay_keeps_interrupted_turn_auditable_but_excludes_its_content() -> None:
    interrupted = MessageEnvelope.text(
        session_id="s1",
        sequence=1,
        source=SourceKind.USER,
        text="Count from one to ten",
        metadata={TURN_ID_METADATA: "turn-1"},
        message_id="user-1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    disposition = MessageEnvelope.text(
        session_id="s1",
        sequence=2,
        source=SourceKind.SYSTEM,
        text="Turn interrupted",
        metadata={
            TURN_ID_METADATA: "turn-1",
            TURN_EVENT_METADATA: "turn_disposition",
            TURN_DISPOSITION_METADATA: "interrupted",
        },
        message_id="disposition-1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    current = MessageEnvelope.text(
        session_id="s1",
        sequence=3,
        source=SourceKind.USER,
        text="What model are you?",
        metadata={TURN_ID_METADATA: "turn-2"},
        message_id="user-2",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    packets = context_replay((interrupted, disposition, current))

    assert [packet.include for packet in packets] == [False, False, True]
    assert "Count from one to ten" not in packets[0].content
    assert packets[2].content == "What model are you?"
    assert dict(packets[0].annotations)[TURN_DISPOSITION_METADATA] == "interrupted"
