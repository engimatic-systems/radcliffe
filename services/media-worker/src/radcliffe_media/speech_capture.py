from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import cast

from radcliffe_media.speech import Transcription, TranscriptionAssessment
from radcliffe_media.speech_quality import normalize_words

CAPTURE_SCHEMA = "radcliffe-stt-capture-v1"
PLAN_SCHEMA = "radcliffe-stt-capture-plan-v1"
REQUIRED_COVERAGE = frozenset(
    {
        "first_turn",
        "multi_sentence",
        "short_command",
        "natural_pause",
        "room_noise",
    }
)
REQUIRED_ROUTES = frozenset({"speaker", "earbud"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_PARTICIPANT_IDENTITY = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


def dropped_leading_word_count(reference: str, hypothesis: str) -> int:
    """Count an exact leading reference run omitted by the hypothesis."""
    expected = normalize_words(reference)
    actual = normalize_words(hypothesis)
    if not expected or not actual:
        return len(expected) if expected and not actual else 0
    maximum = min(len(expected) - 1, 8)
    for dropped in range(maximum + 1):
        comparison_length = min(3, len(expected) - dropped, len(actual))
        if expected[dropped : dropped + comparison_length] == actual[:comparison_length]:
            return dropped
    return 0


def corpus_coverage(cases: tuple[CaptureCase, ...]) -> tuple[frozenset[str], frozenset[str]]:
    return (
        frozenset(item for case in cases for item in case.coverage),
        frozenset(case.route for case in cases),
    )


@dataclass(frozen=True, slots=True)
class CaptureCase:
    case_id: str
    expected: str
    required_keywords: tuple[str, ...]
    coverage: tuple[str, ...]
    route: str

    @classmethod
    def from_json(cls, value: object) -> CaptureCase:
        if not isinstance(value, dict):
            raise ValueError("capture cases must be JSON objects")
        case_id = value.get("id")
        expected = value.get("expected")
        keywords = value.get("required_keywords", [])
        coverage = value.get("coverage", [])
        route = value.get("route")
        if not isinstance(case_id, str) or _IDENTIFIER.fullmatch(case_id) is None:
            raise ValueError("capture case IDs must be safe identifiers")
        if not isinstance(expected, str) or not expected.strip():
            raise ValueError("capture cases require expected text")
        if not isinstance(keywords, list) or not all(
            isinstance(keyword, str) and keyword.strip() for keyword in keywords
        ):
            raise ValueError("required keywords must be non-empty strings")
        if not isinstance(coverage, list) or not all(
            isinstance(item, str) and item in REQUIRED_COVERAGE for item in coverage
        ):
            raise ValueError("capture coverage contains an unsupported value")
        if not isinstance(route, str) or route not in REQUIRED_ROUTES:
            raise ValueError("capture route must be speaker or earbud")
        return cls(
            case_id,
            expected.strip(),
            tuple(cast(list[str], keywords)),
            tuple(cast(list[str], coverage)),
            route,
        )


@dataclass(frozen=True, slots=True)
class CapturePlan:
    owner_identity: str
    cases: tuple[CaptureCase, ...]

    @classmethod
    def load(cls, path: Path) -> CapturePlan:
        if path.is_symlink() or not path.is_file():
            raise ValueError("capture plan must be a regular, non-symlink file")
        if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ValueError("capture plan must not be group or world accessible")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema") != PLAN_SCHEMA:
            raise ValueError(f"capture plan schema must be {PLAN_SCHEMA}")
        owner_identity = value.get("owner_identity")
        raw_cases = value.get("cases")
        if (
            not isinstance(owner_identity, str)
            or _PARTICIPANT_IDENTITY.fullmatch(owner_identity) is None
        ):
            raise ValueError("capture plan requires a valid owner participant identity")
        if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= 24:
            raise ValueError("capture plans must contain between 1 and 24 cases")
        cases = tuple(CaptureCase.from_json(item) for item in raw_cases)
        if len({case.case_id for case in cases}) != len(cases):
            raise ValueError("capture case IDs must be unique")
        return cls(owner_identity, cases)


@dataclass(frozen=True, slots=True)
class EndpointCaptureEvidence:
    endpoint_reason: str
    audio_ms: float
    voice_ms: float
    voice_ratio: float
    pre_roll_ms: float
    trailing_silence_ms: float


class SpeechCapture:
    """Explicit, owner-scoped retention for a bounded private STT corpus."""

    def __init__(
        self,
        plan: CapturePlan,
        output_directory: Path,
        *,
        maximum_audio_ms: float = 30_000,
    ) -> None:
        if maximum_audio_ms <= 0 or maximum_audio_ms > 30_000:
            raise ValueError("captured audio must be bounded to at most 30 seconds")
        self.plan = plan
        self.output_directory = output_directory.resolve()
        self.maximum_audio_ms = maximum_audio_ms
        self.output_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.output_directory.chmod(0o700)
        self._participant_id: str | None = None
        self._track_subscribed_at: float | None = None
        self._first_audio_at: float | None = None
        self._candidate_started_at: float | None = None

    @classmethod
    def from_environment(cls) -> SpeechCapture | None:
        enabled = os.environ.get("RADCLIFFE_STT_CAPTURE_ENABLED")
        if enabled is None or enabled == "0":
            return None
        if enabled != "1":
            raise ValueError("RADCLIFFE_STT_CAPTURE_ENABLED must be 0 or 1")
        plan_path = os.environ.get("RADCLIFFE_STT_CAPTURE_PLAN")
        output_path = os.environ.get("RADCLIFFE_STT_CAPTURE_DIRECTORY")
        if not plan_path or not output_path:
            raise ValueError("STT capture requires explicit plan and output paths")
        return cls(CapturePlan.load(Path(plan_path)), Path(output_path))

    @property
    def remaining(self) -> int:
        return sum(not self._metadata_path(case).exists() for case in self.plan.cases)

    def authorize_participant(self, participant_id: str, identity: str | None) -> bool:
        if identity != self.plan.owner_identity:
            return False
        self._participant_id = participant_id
        return True

    def track_subscribed(self, participant_id: str) -> None:
        if participant_id != self._participant_id:
            return
        self._track_subscribed_at = monotonic()
        self._first_audio_at = None
        self._candidate_started_at = None

    def observe_audio_frame(self, participant_id: str | None) -> None:
        if participant_id != self._participant_id:
            return
        now = monotonic()
        if self._first_audio_at is None:
            self._first_audio_at = now

    def candidate_started(self, participant_id: str | None) -> None:
        if participant_id == self._participant_id:
            self._candidate_started_at = monotonic()

    def should_capture(self, participant_id: str | None) -> bool:
        return participant_id == self._participant_id and self.remaining > 0

    def record(
        self,
        participant_id: str | None,
        pcm: bytes,
        sample_rate: int,
        channels: int,
        transcription: Transcription,
        assessment: TranscriptionAssessment,
        endpoint: EndpointCaptureEvidence,
    ) -> Path | None:
        if not self.should_capture(participant_id):
            return None
        case = next(
            (case for case in self.plan.cases if not self._metadata_path(case).exists()),
            None,
        )
        if case is None:
            return None
        if endpoint.audio_ms > self.maximum_audio_ms:
            raise ValueError("captured endpoint exceeds the configured audio bound")
        if sample_rate <= 0 or channels <= 0:
            raise ValueError("captured audio format must be positive")
        wav_path = self.output_directory / f"{case.case_id}.wav"
        metadata_path = self._metadata_path(case)
        self._write_wav(wav_path, pcm, sample_rate, channels)
        now = monotonic()
        payload = {
            "schema": CAPTURE_SCHEMA,
            "created_at": datetime.now(UTC).isoformat(),
            "id": case.case_id,
            "expected": case.expected,
            "actual": transcription.text,
            "alternative_hypotheses": list(transcription.alternative_texts),
            "required_keywords": list(case.required_keywords),
            "coverage": list(case.coverage),
            "route": case.route,
            "audio": wav_path.name,
            "audio_sha256": hashlib.sha256(pcm).hexdigest(),
            "audio_format": {
                "sample_rate": sample_rate,
                "channels": channels,
                "sample_width_bytes": 2,
            },
            "transport": {
                "track_subscription_to_first_frame_ms": self._elapsed_ms(
                    self._track_subscribed_at,
                    self._first_audio_at,
                ),
                "first_frame_to_candidate_ms": self._elapsed_ms(
                    self._first_audio_at,
                    self._candidate_started_at,
                ),
            },
            "endpoint": {
                "reason": endpoint.endpoint_reason,
                "audio_ms": round(endpoint.audio_ms, 2),
                "voice_ms": round(endpoint.voice_ms, 2),
                "voice_ratio": round(endpoint.voice_ratio, 4),
                "pre_roll_ms": round(endpoint.pre_roll_ms, 2),
                "trailing_silence_ms": round(endpoint.trailing_silence_ms, 2),
                "candidate_to_capture_ms": self._elapsed_ms(self._candidate_started_at, now),
            },
            "decoder": {
                "accepted": assessment.accepted,
                "reason": assessment.reason,
                "segment_count": assessment.segment_count,
                "average_log_probability": assessment.average_log_probability,
                "maximum_no_speech_probability": assessment.maximum_no_speech_probability,
                "maximum_compression_ratio": assessment.maximum_compression_ratio,
                "maximum_hypothesis_word_error_rate": (
                    assessment.maximum_hypothesis_word_error_rate
                ),
            },
        }
        self._write_private_json(metadata_path, payload)
        self._candidate_started_at = None
        return metadata_path

    def _metadata_path(self, case: CaptureCase) -> Path:
        return self.output_directory / f"{case.case_id}.json"

    @staticmethod
    def _elapsed_ms(start: float | None, end: float | None) -> float | None:
        if start is None or end is None:
            return None
        return round(max(0.0, end - start) * 1000, 2)

    @staticmethod
    def _write_wav(path: Path, pcm: bytes, sample_rate: int, channels: int) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as raw:
            with wave.open(raw, "wb") as output:
                output.setnchannels(channels)
                output.setsampwidth(2)
                output.setframerate(sample_rate)
                output.writeframes(pcm)

    @staticmethod
    def _write_private_json(path: Path, value: object) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2)
            output.write("\n")
