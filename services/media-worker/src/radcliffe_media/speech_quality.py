from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def normalize_words(text: str) -> tuple[str, ...]:
    return tuple(_WORD.findall(text.casefold()))


def word_error_rate(reference: str, hypothesis: str) -> float:
    expected = normalize_words(reference)
    actual = normalize_words(hypothesis)
    if not expected:
        return 0.0 if not actual else 1.0
    previous = list(range(len(actual) + 1))
    for row, expected_word in enumerate(expected, start=1):
        current = [row] + [0] * len(actual)
        for column, actual_word in enumerate(actual, start=1):
            current[column] = min(
                previous[column] + 1,
                current[column - 1] + 1,
                previous[column - 1] + (expected_word != actual_word),
            )
        previous = current
    return previous[-1] / len(expected)


def keyword_recall(required: tuple[str, ...], hypothesis: str) -> float:
    if not required:
        return 1.0
    actual = normalize_words(hypothesis)
    hits = 0
    for keyword in required:
        phrase = normalize_words(keyword)
        if phrase and any(
            actual[start : start + len(phrase)] == phrase
            for start in range(len(actual) - len(phrase) + 1)
        ):
            hits += 1
    return hits / len(required)


@dataclass(frozen=True, slots=True)
class SpeechQuality:
    word_error_rate: float
    keyword_recall: float
    passed: bool


@dataclass(frozen=True, slots=True)
class SpeechQualitySummary:
    passed: bool
    macro_word_error_rate: float
    minimum_keyword_recall: float
    median_latency_seconds: float
    maximum_latency_seconds: float


def summarize_quality(results: list[SpeechQuality], latencies: list[float]) -> SpeechQualitySummary:
    if not results or len(results) != len(latencies):
        raise ValueError("results and latencies must be non-empty and have equal lengths")
    return SpeechQualitySummary(
        passed=all(result.passed for result in results),
        macro_word_error_rate=sum(result.word_error_rate for result in results) / len(results),
        minimum_keyword_recall=min(result.keyword_recall for result in results),
        median_latency_seconds=statistics.median(latencies),
        maximum_latency_seconds=max(latencies),
    )


def assess_transcript(
    reference: str,
    hypothesis: str,
    required_keywords: tuple[str, ...] = (),
    *,
    maximum_word_error_rate: float = 0.2,
    minimum_keyword_recall: float = 1.0,
) -> SpeechQuality:
    error_rate = word_error_rate(reference, hypothesis)
    recall = keyword_recall(required_keywords, hypothesis)
    return SpeechQuality(
        word_error_rate=error_rate,
        keyword_recall=recall,
        passed=error_rate <= maximum_word_error_rate and recall >= minimum_keyword_recall,
    )
