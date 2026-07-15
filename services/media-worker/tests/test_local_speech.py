import sys

import numpy as np
import pytest
from radcliffe_media.speech import (
    LocalAppleSpeechProvider,
    Transcription,
    TranscriptionSegmentEvidence,
    assess_transcription,
)
from radcliffe_media.speech_activity import SileroSpeechActivityAnalyzer


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS local voice")
async def test_local_tts_produces_24khz_mono_pcm() -> None:
    provider = LocalAppleSpeechProvider(voice="Samantha")

    audio = await provider.synthesize("- **Cosmic touch:** Moon 🌙\n-- still canonical text")

    assert len(audio) > 2400
    assert len(audio) % 2 == 0


@pytest.mark.asyncio
async def test_local_whisper_receives_an_in_memory_16khz_mono_waveform(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_transcribe(audio: np.ndarray, **options):
        seen["audio"] = audio
        seen["options"] = options
        return {"text": " local transcript "}

    monkeypatch.setattr("radcliffe_media.speech.mlx_whisper.transcribe", fake_transcribe)
    provider = LocalAppleSpeechProvider(whisper_model="local-test-model")

    stereo_8khz = np.tile(np.array([16384, 16384], dtype="<i2"), 800).tobytes()
    transcript = await provider.transcribe(stereo_8khz, 8000, 2)

    assert transcript == "local transcript"
    audio = seen["audio"]
    assert isinstance(audio, np.ndarray)
    assert audio.shape == (1600,)
    assert audio.dtype == np.float32
    assert np.allclose(audio, 0.5)
    assert seen["options"] == {
        "path_or_hf_repo": "local-test-model",
        "language": "en",
        "condition_on_previous_text": False,
        "no_speech_threshold": 0.7,
        "initial_prompt": (
            "Radcliffe vocabulary: dash, bold, Markdown, Cosmic touch, voice controls work, "
            "read source, metadata."
        ),
    }


@pytest.mark.parametrize(
    ("segment", "expected_reason"),
    [
        (
            TranscriptionSegmentEvidence(float("nan"), 0.01, 0.5),
            "non_finite_decoder_evidence",
        ),
        (TranscriptionSegmentEvidence(-0.4, 0.01, 31.8), "repetitive_decode"),
        (TranscriptionSegmentEvidence(-1.7, 0.01, 0.5), "low_log_probability"),
        (TranscriptionSegmentEvidence(-0.4, 0.8, 0.5), "no_speech_probability"),
    ],
)
def test_whisper_decoder_evidence_rejects_low_information_transcripts(
    segment: TranscriptionSegmentEvidence,
    expected_reason: str,
) -> None:
    assessment = assess_transcription(Transcription("Thank you.", (segment,)))

    assert not assessment.accepted
    assert assessment.reason == expected_reason


def test_legitimate_thank_you_is_not_phrase_blacklisted() -> None:
    assessment = assess_transcription(
        Transcription(
            "Thank you.",
            (TranscriptionSegmentEvidence(-0.54, 0.01, 0.56),),
        )
    )

    assert assessment.accepted
    assert assessment.reason == "accepted"


@pytest.mark.asyncio
async def test_silero_rejects_deterministic_room_noise() -> None:
    random = np.random.default_rng(20260714)
    noise = (random.normal(0, 0.04, 26400) * 32767).astype("<i2").tobytes()

    evidence = await SileroSpeechActivityAnalyzer().analyze(noise, 16000, 1)

    assert evidence.speech_window_count == 0
    assert evidence.maximum_probability < 0.5


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS local voice")
@pytest.mark.parametrize(
    ("voice", "phrase"),
    [
        ("Samantha", "Go."),
        ("Daniel", "Stop."),
        ("Moira", "No."),
        ("Karen", "Yes."),
        ("Tessa", "Stop."),
        ("Fred", "Thank you."),
    ],
)
async def test_silero_retains_legitimate_short_speech(voice: str, phrase: str) -> None:
    provider = LocalAppleSpeechProvider(voice=voice)
    audio = await provider.synthesize(phrase)

    evidence = await SileroSpeechActivityAnalyzer().analyze(audio, 24000, 1)

    assert evidence.speech_window_count >= 2
    assert evidence.maximum_probability >= 0.8


def test_local_waveform_rejects_invalid_audio_metadata() -> None:
    with pytest.raises(ValueError, match="positive"):
        LocalAppleSpeechProvider._waveform(b"", 0, 1)
