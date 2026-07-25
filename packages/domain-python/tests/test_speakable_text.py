"""Markdown source rendered as the words a listener should hear.

Markup is an encoding, not the author's prose. The rule these tests pin down is that link
*syntax* is parsed and resolved to its label, never stripped character by character: a bare
parenthetical or a bracketed aside matches no link grammar and must survive untouched.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from radcliffe_domain.envelope import MessageEnvelope, SourceKind
from radcliffe_domain.projection import _sentences, render_projection, speakable_text

GWERN_FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "sources" / "gwern-nicotine.md"


@pytest.mark.parametrize(
    ("markup", "spoken"),
    [
        ("See [the overview](https://example.com/a) for more.", "See the overview for more."),
        ("A [relative link](/doc/iq/index) here.", "A relative link here."),
        ("An [anchor](#addictiveness) here.", "An anchor here."),
        ("A [reference link][ref] here.", "A reference link here."),
        ("An ![image alt](https://example.com/i.png) here.", "An image alt here."),
        ("Visit <https://example.com/page> now.", "Visit now."),
        ("Visit https://example.com/page now.", "Visit now."),
        ("Contact www.example.com now.", "Contact now."),
        ("This is **bold** text.", "This is bold text."),
        ("This is __bold__ text.", "This is bold text."),
        ("This is *italic* text.", "This is italic text."),
        ("This is _italic_ text.", "This is italic text."),
        ("This is ***both*** text.", "This is both text."),
        ("Use the `read_source` tool.", "Use the read_source tool."),
    ],
)
def test_markup_is_resolved_to_the_words_underneath(markup: str, spoken: str) -> None:
    assert speakable_text(markup) == spoken


@pytest.mark.parametrize(
    "prose",
    [
        "Nicotine was popular in the 1600s (along with tea and coffee).",
        "The result [sic] was unexpected.",
        "Cost-wise (see below), the two are conflated.",
        "He said [and I quote] that it works.",
        "A sentence with no markup at all.",
        "Multiply 3 * 4 to get twelve.",
        "The file is named report_final_v2 on disk.",
        "Nested (parentheses (like these)) are fine.",
    ],
)
def test_real_prose_survives_verbatim(prose: str) -> None:
    """The constraint that rules out character-level stripping of brackets and parentheses."""

    assert speakable_text(prose) == prose


def test_a_word_run_into_a_link_does_not_lose_its_space() -> None:
    # Trafilatura drops the space when it unwraps an anchor, which is why this was
    # heard as one run-together word.
    assert speakable_text("it affects[tons of systems](https://example.com) .") == (
        "it affects tons of systems."
    )
    assert speakable_text("see [the docs](https://example.com)and stop") == (
        "see the docs and stop"
    )


def test_spacing_is_repaired_only_where_markup_was_removed() -> None:
    assert speakable_text("a [link](https://example.com) sentence .") == "a link sentence."
    assert speakable_text("a **bold** clause , then more") == "a bold clause, then more"


@pytest.mark.parametrize(
    "prose",
    [
        "a sentence .",
        "a clause , then more",
        "too    many spaces",
        "( leading space )",
        "First exact sentence.  Second exact sentence.\n",
        "  leading and trailing  ",
    ],
)
def test_text_without_markup_is_returned_byte_for_byte(prose: str) -> None:
    """Spacing belongs to the source. Repair is cleanup after markup removal, nothing more.

    This is what keeps the exact-source guarantee true for plain prose: if there is no
    markup to resolve, the normalizer is a no-op rather than a tidier.
    """

    assert speakable_text(prose) == prose


def test_a_link_keeps_its_label_even_when_the_label_is_navigation() -> None:
    """Normalization resolves syntax; it does not judge which words are worth saying.

    Deciding that "Skip to main content" is chrome is an extraction-time call. Making it a
    render-time heuristic would risk deleting real prose that happens to resemble a nav label.
    """

    assert speakable_text("[Skip to main content](https://example.com/#main)") == (
        "Skip to main content"
    )
    assert speakable_text("- [Addictiveness](#addictiveness)") == "- Addictiveness"


def test_content_that_was_only_a_url_normalizes_away_entirely() -> None:
    assert speakable_text("<https://example.com/page>") == ""
    assert speakable_text("https://example.com/page") == ""


def test_reference_definitions_are_dropped() -> None:
    assert speakable_text("[ref]: https://example.com/target") == ""


def test_normalization_is_idempotent_and_safe_on_empty_input() -> None:
    once = speakable_text("A [link](https://example.com) and **bold**.")
    assert speakable_text(once) == once
    assert speakable_text("") == ""


def source(text: str, kind: SourceKind = SourceKind.TEXT_SOURCE) -> MessageEnvelope:
    return MessageEnvelope.text(
        session_id="s1", sequence=1, source=kind, text=text, message_id="m1"
    )


def test_render_plan_speaks_the_normalized_text_for_sources() -> None:
    plan = render_projection(source("See [the overview](https://example.com) now."))

    assert [segment.canonical_text for segment in plan.segments] == ["See the overview now."]


def test_model_speech_is_left_alone() -> None:
    """Normalization is a source-reading concern; model turns are not markdown documents."""

    spoken = "This is **not** touched."
    plan = render_projection(source(spoken, SourceKind.MODEL))

    assert [segment.canonical_text for segment in plan.segments] == [spoken]


def test_the_link_heavy_fixture_reaches_speech_free_of_markup() -> None:
    """Regression against the real extraction shape that made gwern.net unlistenable.

    Asserted structurally rather than by matching text, so the fixture can be replaced
    without rewriting expectations.
    """

    raw = GWERN_FIXTURE.read_text()
    sentences = _sentences(raw)
    spoken = [speakable_text(sentence) for sentence in sentences]

    assert len(re.findall(r"\[[^\]\n]*\]\([^)\n]*\)", raw)) > 20, "fixture should be link-heavy"
    for original, sentence in zip(sentences, spoken, strict=True):
        assert not re.search(r"\[[^\]\n]*\]\([^)\n]*\)", sentence)
        assert "http://" not in sentence and "https://" not in sentence
        assert "**" not in sentence
        if sentence != original:
            # Repair applies where markup was removed. A sentence with no markup keeps its
            # own spacing, quirks included, because that spacing is part of the source.
            assert not re.search(r"\s[.,;:]", sentence)
    # Splitting happens before normalization, so recorded positions still address the
    # same sentence of the same document.
    assert len(spoken) == len(sentences)


def test_boundary_indices_stay_anchored_to_the_canonical_document() -> None:
    """Positions recorded before normalization existed must still address the same sentence."""

    text = "First [link](https://example.com) sentence. Second sentence. Third sentence."
    plan = render_projection(source(text))

    assert len(plan.segments) == len(_sentences(text))
    assert [segment.boundary_index for segment in plan.segments] == [0, 1, 2]
    assert plan.segments[0].canonical_text.startswith("First link sentence.")
