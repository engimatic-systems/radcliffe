from datetime import UTC, datetime

import pytest
from radcliffe_domain.session import (
    ConversationState,
    PlaybackAcknowledgement,
    PlaybackStatus,
    SessionEngine,
    StaleOperationError,
)
from radcliffe_domain.store import EventStore


@pytest.mark.asyncio
async def test_interrupt_invalidates_output_and_playback_boundary_is_durable(tmp_path) -> None:
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    engine = SessionEngine("s1", store)
    engine.connect()
    engine.ready()
    engine.begin(ConversationState.THINKING)
    speaking = engine.begin(ConversationState.SPEAKING)
    ack = PlaybackAcknowledgement(
        acknowledgement_id="ack-1",
        operation=speaking,
        segment_id="segment-1",
        status=PlaybackStatus.POSITION,
        position_ms=830,
        boundary_index=2,
        occurred_at=datetime.now(UTC),
    )
    assert await engine.acknowledge(ack)
    assert not await engine.acknowledge(ack)
    assert await store.playback_boundary("s1", "segment-1") == 2

    engine.interrupt()
    assert engine.state is ConversationState.LISTENING
    with pytest.raises(StaleOperationError):
        await engine.acknowledge(
            PlaybackAcknowledgement(
                acknowledgement_id="ack-stale",
                operation=speaking,
                segment_id="segment-1",
                status=PlaybackStatus.FLUSHED,
                position_ms=900,
                boundary_index=2,
                occurred_at=datetime.now(UTC),
            )
        )


def test_reading_pause_resume_and_end_are_explicit(tmp_path) -> None:
    engine = SessionEngine("s1", EventStore(tmp_path / "events.sqlite3"))
    engine.connect()
    engine.ready()
    engine.begin(ConversationState.THINKING)
    engine.begin(ConversationState.READING)
    engine.pause()
    assert engine.state is ConversationState.PAUSED
    assert engine.resume() is ConversationState.READING
    engine.end()
    assert engine.state is ConversationState.IDLE


def test_listening_session_can_pause_without_an_active_playback(tmp_path) -> None:
    engine = SessionEngine("s1", EventStore(tmp_path / "events.sqlite3"))
    engine.connect()
    engine.ready()

    engine.pause()

    assert engine.state is ConversationState.PAUSED
    assert engine.resume() is ConversationState.LISTENING
