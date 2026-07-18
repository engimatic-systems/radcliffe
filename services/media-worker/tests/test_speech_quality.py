import pytest
from radcliffe_media.speech_quality import (
    SpeechQuality,
    assess_transcript,
    keyword_recall,
    normalize_words,
    summarize_quality,
    word_error_rate,
)


def test_normalization_and_word_error_rate_are_deterministic() -> None:
    reference = "Start with dash, bold Cosmic touch."

    assert normalize_words(reference) == ("start", "with", "dash", "bold", "cosmic", "touch")
    assert word_error_rate(reference, "Start with dash bold cosmic touch") == 0
    assert word_error_rate(reference, "Start with dash old cosmic touch") == 1 / 6


def test_required_command_vocabulary_can_fail_an_otherwise_close_transcript() -> None:
    result = assess_transcript(
        "Start with dash, bold Cosmic touch, then give me a moon fact.",
        "Start with dash, old Cosmic touch, then give me a moon fact.",
        ("dash", "bold", "Cosmic touch", "moon"),
    )

    assert result.word_error_rate < 0.2
    assert result.keyword_recall == 0.75
    assert not result.passed


def test_keyword_recall_supports_multiword_phrases() -> None:
    assert keyword_recall(("cosmic touch", "moon"), "A Moon has the cosmic touch") == 1
    assert keyword_recall(("cosmic touch",), "Cosmic details are nice to touch") == 0


def test_quality_report_summary_aggregates_cases_and_latencies() -> None:
    summary = summarize_quality(
        [
            SpeechQuality(word_error_rate=0, keyword_recall=1, passed=True),
            SpeechQuality(word_error_rate=0.1, keyword_recall=1, passed=True),
            SpeechQuality(word_error_rate=0.2, keyword_recall=0.75, passed=False),
        ],
        [0.4, 0.2, 0.3],
    )

    assert not summary.passed
    assert summary.macro_word_error_rate == pytest.approx(0.1)
    assert summary.minimum_keyword_recall == 0.75
    assert summary.median_latency_seconds == 0.3
    assert summary.maximum_latency_seconds == 0.4
