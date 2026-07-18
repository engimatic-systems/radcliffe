import json
import os
import stat
import wave
from pathlib import Path

import pytest
from radcliffe_media.speech import (
    Transcription,
    TranscriptionSegmentEvidence,
    assess_transcription,
)
from radcliffe_media.speech_capture import (
    CAPTURE_SCHEMA,
    PLAN_SCHEMA,
    CapturePlan,
    EndpointCaptureEvidence,
    SpeechCapture,
    corpus_coverage,
    dropped_leading_word_count,
)


def write_plan(path: Path, *, owner: str = "radcliffe-dev_owner") -> None:
    path.write_text(
        json.dumps(
            {
                "schema": PLAN_SCHEMA,
                "owner_identity": owner,
                "cases": [
                    {
                        "id": "first-turn-speaker",
                        "expected": "Please turn on the kitchen lights.",
                        "required_keywords": ["kitchen lights"],
                        "coverage": ["first_turn", "short_command", "room_noise"],
                        "route": "speaker",
                    },
                    {
                        "id": "multi-sentence-earbud",
                        "expected": "First sentence. Second sentence after a pause.",
                        "required_keywords": ["first sentence", "second sentence"],
                        "coverage": ["multi_sentence", "natural_pause"],
                        "route": "earbud",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_capture_is_disabled_unless_explicitly_opted_in(monkeypatch) -> None:
    monkeypatch.delenv("RADCLIFFE_STT_CAPTURE_ENABLED", raising=False)

    assert SpeechCapture.from_environment() is None


def test_capture_requires_owner_identity_and_private_bounded_files(tmp_path) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    output = tmp_path / "private-corpus"
    capture = SpeechCapture(CapturePlan.load(plan_path), output)

    assert not capture.authorize_participant("sid-attacker", "another-device")
    assert not capture.should_capture("sid-attacker")
    assert capture.authorize_participant("sid-owner", "radcliffe-dev_owner")
    capture.track_subscribed("sid-owner")
    capture.observe_audio_frame("sid-owner")
    capture.candidate_started("sid-owner")
    assert capture.should_capture("sid-owner")

    pcm = b"\x01\x00" * 1600
    transcription = Transcription(
        "Please turn on the kitchen lights.",
        (TranscriptionSegmentEvidence(-0.4, 0.01, 0.5),),
    )
    metadata_path = capture.record(
        "sid-owner",
        pcm,
        16000,
        1,
        transcription,
        assess_transcription(transcription),
        EndpointCaptureEvidence("silence", 100, 80, 0.8, 20, 20),
    )

    assert metadata_path == output / "first-turn-speaker.json"
    wav_path = output / "first-turn-speaker.wav"
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE(metadata_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(wav_path.stat().st_mode) == 0o600
    with wave.open(str(wav_path), "rb") as audio:
        assert audio.getframerate() == 16000
        assert audio.getnchannels() == 1
        assert audio.readframes(audio.getnframes()) == pcm
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["schema"] == CAPTURE_SCHEMA
    assert metadata["expected"] == "Please turn on the kitchen lights."
    assert metadata["actual"] == transcription.text
    assert metadata["transport"]["track_subscription_to_first_frame_ms"] is not None
    assert metadata["endpoint"]["pre_roll_ms"] == 20
    assert capture.remaining == 1


def test_capture_rejects_audio_over_thirty_seconds(tmp_path) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    capture = SpeechCapture(CapturePlan.load(plan_path), tmp_path / "output")
    capture.authorize_participant("sid-owner", "radcliffe-dev_owner")
    transcription = Transcription("Expected")

    with pytest.raises(ValueError, match="audio bound"):
        capture.record(
            "sid-owner",
            b"",
            16000,
            1,
            transcription,
            assess_transcription(transcription),
            EndpointCaptureEvidence("maximum_utterance", 30_001, 1000, 0.5, 0, 0),
        )


def test_capture_environment_requires_both_explicit_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RADCLIFFE_STT_CAPTURE_ENABLED", "1")
    monkeypatch.delenv("RADCLIFFE_STT_CAPTURE_PLAN", raising=False)
    monkeypatch.delenv("RADCLIFFE_STT_CAPTURE_DIRECTORY", raising=False)

    with pytest.raises(ValueError, match="explicit plan and output"):
        SpeechCapture.from_environment()

    assert not any(tmp_path.iterdir())


def test_capture_plan_rejects_group_or_world_access(tmp_path) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    plan_path.chmod(0o644)

    with pytest.raises(ValueError, match="group or world"):
        CapturePlan.load(plan_path)


def test_capture_files_are_not_group_or_world_readable(tmp_path) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    capture = SpeechCapture(CapturePlan.load(plan_path), tmp_path / "output")
    capture.authorize_participant("sid-owner", "radcliffe-dev_owner")
    transcription = Transcription("Please turn on the kitchen lights.")
    path = capture.record(
        "sid-owner",
        b"\x00\x00" * 160,
        16000,
        1,
        transcription,
        assess_transcription(transcription),
        EndpointCaptureEvidence("silence", 10, 10, 1, 0, 0),
    )

    assert path is not None
    assert path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
    assert os.stat(path.with_suffix(".wav")).st_mode & (stat.S_IRWXG | stat.S_IRWXO) == 0


def test_private_corpus_reports_coverage_and_dropped_leading_words(tmp_path) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    plan = CapturePlan.load(plan_path)

    coverage, routes = corpus_coverage(plan.cases)

    assert coverage == {
        "first_turn",
        "multi_sentence",
        "short_command",
        "natural_pause",
        "room_noise",
    }
    assert routes == {"speaker", "earbud"}
    assert (
        dropped_leading_word_count(
            "Please turn on the kitchen lights",
            "on the kitchen lights",
        )
        == 2
    )
    assert (
        dropped_leading_word_count(
            "Please turn on the kitchen lights",
            "Please turn on the kitchen lights",
        )
        == 0
    )
