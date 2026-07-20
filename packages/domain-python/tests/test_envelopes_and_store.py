from datetime import UTC, datetime

import pytest
from radcliffe_domain.envelope import MessageEnvelope, Sensitivity, SourceKind
from radcliffe_domain.projection import (
    CONFIGURATION_EVENT,
    DELIVERY_SUMMARY_METADATA,
    TOOL_FAILURE_EVENT,
    TURN_DISPOSITION_METADATA,
    TURN_EVENT_METADATA,
    TURN_ID_METADATA,
    SpeechDelivery,
    context_projection,
    context_replay,
    project_replay,
    render_projection,
    with_delivery_rate,
    with_delivery_rate_for_all,
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
    first_page = await store.replay_page("s1", limit=1)
    assert first_page.items == (first,)
    assert first_page.next_after == 1
    second_page = await store.replay_page("s1", after=first_page.next_after, limit=1)
    assert second_page.items == (second,)
    assert second_page.next_after is None

    out_of_order = MessageEnvelope.text(
        session_id="s1", sequence=4, source=SourceKind.USER, text="skip"
    )
    with pytest.raises(StoreConflictError, match="expected sequence 3"):
        await store.append(out_of_order)
    with pytest.raises(ValueError, match="between 1 and 100"):
        await store.replay_page("s1", limit=101)


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
    assert context_projection(source).to_wire(1_767_225_600_000) == {
        "projectionVersion": "context-v2",
        "messageId": "source-1",
        "role": "source",
        "content": "First sentence. Second sentence!",
        "source": "text_source",
        "annotations": [
            ["format", "markdown"],
            ["locator", ""],
            ["permission_basis", ""],
            ["projection", "context-v2"],
            ["source", "text_source"],
        ],
        "include": True,
        "timestamp": 1_767_225_600_000,
    }


@pytest.mark.parametrize(
    ("value", "multiplier", "words_per_minute"),
    [
        ("slow", 0.75, 135),
        ("normal", 1.0, 180),
        ("fast", 1.25, 225),
        ("very fast", 1.5, 270),
        (1.333, 1.333, 240),
    ],
)
def test_speech_delivery_rates_are_bounded_and_map_deterministically(
    value: object,
    multiplier: float,
    words_per_minute: int,
) -> None:
    delivery = SpeechDelivery.parse(value)

    assert delivery.multiplier == multiplier
    assert delivery.local_words_per_minute == words_per_minute


@pytest.mark.parametrize("value", [False, float("nan"), 0.749, 1.501, "unbounded"])
def test_speech_delivery_rejects_invalid_or_unbounded_rates(value: object) -> None:
    with pytest.raises(ValueError, match="speech rate|unsupported speech rate"):
        SpeechDelivery.parse(value)


def test_delivery_metadata_preserves_canonical_identity_and_stays_out_of_context() -> None:
    source = MessageEnvelope.text(
        session_id="s1",
        sequence=1,
        source=SourceKind.TEXT_SOURCE,
        text="First sentence. Second sentence!",
        message_id="source-rate",
    )

    delivered = with_delivery_rate_for_all(source, "fast")
    plan = render_projection(delivered)

    assert delivered.content == source.content
    assert delivered.content_sha256 == source.content_sha256
    assert (
        "".join(segment.canonical_text for segment in plan.segments) == source.content.inline_text
    )
    assert [segment.delivery.multiplier for segment in plan.segments] == [1.25, 1.25]
    assert plan.delivery_summary == "Fast (1.25×)"
    assert delivered.metadata[DELIVERY_SUMMARY_METADATA] == "Fast (1.25×)"
    assert all(
        "radcliffe.delivery" not in key for key, _ in context_projection(delivered).annotations
    )
    assert render_projection(delivered) == render_projection(delivered)


def test_delivery_rate_is_boundary_scoped_and_does_not_leak() -> None:
    source = MessageEnvelope.text(
        session_id="s1",
        sequence=1,
        source=SourceKind.TEXT_SOURCE,
        text="One. Two. Three.",
    )

    mixed = with_delivery_rate(source, (1,), 1.5)
    normal = MessageEnvelope.text(
        session_id="s1",
        sequence=2,
        source=SourceKind.TEXT_SOURCE,
        text="Next source.",
    )

    assert [item.delivery.multiplier for item in render_projection(mixed).segments] == [
        1.0,
        1.5,
        1.0,
    ]
    assert render_projection(mixed).delivery_summary == "Very fast (1.5×)"
    assert [item.delivery.multiplier for item in render_projection(normal).segments] == [1.0]
    with pytest.raises(ValueError, match="exceeds"):
        with_delivery_rate(source, (3,), "fast")

    explicit_normal = with_delivery_rate(source, (0,), "normal")
    first = render_projection(explicit_normal).segments[0]
    assert first.delivery.is_default
    assert first.delivery_overridden


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


def test_configuration_audit_is_durable_but_not_model_visible() -> None:
    audit = MessageEnvelope.text(
        session_id="s1",
        sequence=1,
        source=SourceKind.SYSTEM,
        text="Behavior setting updated",
        metadata={
            TURN_EVENT_METADATA: CONFIGURATION_EVENT,
            "radcliffe.configuration.key": "response_detail",
            "radcliffe.configuration.value": "brief",
        },
    )

    packet = context_projection(audit)

    assert not packet.include
    assert "Behavior setting updated" not in packet.content
    assert packet.content == "[excluded: configuration audit]"


def test_tool_failure_audit_is_durable_but_not_model_visible() -> None:
    audit = MessageEnvelope.text(
        session_id="s1",
        sequence=1,
        source=SourceKind.SYSTEM,
        text="Tool call failed",
        metadata={
            TURN_EVENT_METADATA: TOOL_FAILURE_EVENT,
            "radcliffe.tool.name": "manage_job",
            "radcliffe.tool.outcome": "failed",
        },
    )

    packet = context_projection(audit)

    assert not packet.include
    assert "Tool call failed" not in packet.content
    assert packet.content == "[excluded: tool failure audit]"
