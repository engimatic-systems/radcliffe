"""Content-keyed resume position, derived from the durable heard ledger.

Playback segment IDs embed the tool call ID, so they cannot answer "how far did the user
get" once a second read_source call opens a new namespace for the same document. The
heard ledger records a content hash per sentence, which does survive that.
"""

from __future__ import annotations

import hashlib

from radcliffe_domain.envelope import MessageEnvelope, SourceKind
from radcliffe_domain.projection import (
    PLAYBACK_BOUNDARY_INDEX_METADATA,
    PLAYBACK_COMPLETE_METADATA,
    SOURCE_CONTENT_SHA256_METADATA,
    SOURCE_PLAYBACK_EVENT,
    TURN_EVENT_METADATA,
    heard_boundary,
    resume_boundary,
)

ARTICLE = hashlib.sha256(b"nicotine").digest()
OTHER = hashlib.sha256(b"something else").digest()


def heard(
    sequence: int,
    boundary_index: int,
    *,
    digest: bytes = ARTICLE,
    complete: bool = True,
    call_id: str = "toolu_a",
) -> MessageEnvelope:
    return MessageEnvelope.text(
        session_id="s1",
        sequence=sequence,
        source=SourceKind.TEXT_SOURCE,
        text="a heard sentence",
        message_id=f"heard:{call_id}:{sequence}",
        metadata={
            TURN_EVENT_METADATA: SOURCE_PLAYBACK_EVENT,
            SOURCE_CONTENT_SHA256_METADATA: digest.hex(),
            PLAYBACK_BOUNDARY_INDEX_METADATA: str(boundary_index),
            PLAYBACK_COMPLETE_METADATA: "true" if complete else "false",
        },
    )


def test_a_completed_sentence_resumes_at_the_next_one() -> None:
    assert heard_boundary(heard(1, 9).metadata) == 10


def test_a_partially_heard_sentence_replays_from_its_own_start() -> None:
    assert heard_boundary(heard(1, 9, complete=False).metadata) == 9


def test_unrelated_envelopes_carry_no_boundary() -> None:
    plain = MessageEnvelope.text(session_id="s1", sequence=1, source=SourceKind.USER, text="Stop.")
    assert heard_boundary(plain.metadata) is None
    malformed = dict(heard(1, 9).metadata) | {PLAYBACK_BOUNDARY_INDEX_METADATA: "ten"}
    assert heard_boundary(malformed) is None
    negative = dict(heard(1, 9).metadata) | {PLAYBACK_BOUNDARY_INDEX_METADATA: "-1"}
    assert heard_boundary(negative) is None


def test_resume_position_survives_a_new_tool_call_for_the_same_document() -> None:
    envelopes = tuple(heard(index + 1, index, call_id="toolu_a") for index in range(10))
    # A later call reads the same bytes under a fresh call ID and hears nothing yet.
    assert resume_boundary(envelopes, ARTICLE) == 10


def test_position_is_scoped_to_one_document() -> None:
    envelopes = (
        heard(1, 0, digest=ARTICLE),
        heard(2, 40, digest=OTHER),
        heard(3, 1, digest=ARTICLE),
    )
    assert resume_boundary(envelopes, ARTICLE) == 2
    assert resume_boundary(envelopes, OTHER) == 41


def test_the_furthest_point_wins_over_a_later_rewind() -> None:
    # Backing up to reread does not erase how far the user has already got.
    envelopes = (heard(1, 9), heard(2, 3, call_id="toolu_b"))
    assert resume_boundary(envelopes, ARTICLE) == 10


def test_an_unheard_document_starts_at_the_beginning() -> None:
    assert resume_boundary((), ARTICLE) == 0
    assert resume_boundary((heard(1, 5, digest=OTHER),), ARTICLE) == 0
    assert resume_boundary((heard(1, 5),), b"") == 0
