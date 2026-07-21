import asyncio
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from radcliffe_media.speech import (
    LocalAppleSpeechProvider,
    OpenAISpeechProvider,
    ParakeetAppleSpeechProvider,
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
@pytest.mark.parametrize(
    ("rate", "expected_words_per_minute"),
    [(0.75, "135"), (1.0, "180"), (1.25, "225"), (1.5, "270")],
)
async def test_local_tts_maps_bounded_rates_without_changing_input_text(
    monkeypatch,
    rate: float,
    expected_words_per_minute: str,
) -> None:
    calls: list[tuple[str, ...]] = []
    captured_text = ""

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_subprocess(*arguments, **_options):
        nonlocal captured_text
        values = tuple(str(argument) for argument in arguments)
        calls.append(values)
        if values[0] == "/usr/bin/say":
            captured_text = await asyncio.to_thread(Path(values[values.index("-f") + 1]).read_text)
        else:
            await asyncio.to_thread(Path(values[-1]).write_bytes, b"\x00" * 4800)
        return FakeProcess()

    monkeypatch.setattr(
        "radcliffe_media.speech.asyncio.create_subprocess_exec",
        fake_subprocess,
    )
    provider = LocalAppleSpeechProvider(voice="Test Voice")

    audio = await provider.synthesize("Exact source.\n", rate=rate)

    assert captured_text == "Exact source.\n"
    assert calls[0][calls[0].index("-r") + 1] == expected_words_per_minute
    assert audio == b"\x00" * 4800


@pytest.mark.asyncio
async def test_openai_tts_receives_the_same_bounded_rate(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class FakeResponse:
        content = b"pcm"

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url, *, headers, json):
            seen.update(url=url, headers=headers, json=json)
            return FakeResponse()

    monkeypatch.setattr(
        "radcliffe_media.speech.httpx.AsyncClient",
        lambda **_options: FakeClient(),
    )
    provider = OpenAISpeechProvider("test-key")

    audio = await provider.synthesize("Exact source.", rate=1.333)

    assert audio == b"pcm"
    assert seen["json"] == {
        "model": "tts-1",
        "voice": "alloy",
        "input": "Exact source.",
        "speed": 1.333,
        "response_format": "pcm",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [0.749, 1.501, float("inf")])
async def test_speech_providers_reject_out_of_bounds_rates(rate: float) -> None:
    with pytest.raises(ValueError, match="between"):
        await LocalAppleSpeechProvider().synthesize("No call.", rate=rate)
    with pytest.raises(ValueError, match="between"):
        await OpenAISpeechProvider("test-key").synthesize("No call.", rate=rate)


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
        "temperature": 0.0,
        "initial_prompt": (
            "Radcliffe vocabulary: dash, bold, Markdown, Cosmic touch, voice controls work, "
            "read source, metadata."
        ),
    }


@pytest.mark.asyncio
async def test_local_parakeet_is_preloaded_and_returns_confidence_evidence(monkeypatch) -> None:
    model_loads: list[str] = []
    waveforms: list[np.ndarray] = []

    class FakeParakeet:
        preprocessor_config = SimpleNamespace(sample_rate=16000, hop_length=160)

        def generate(self, _mel):
            if len(waveforms) == 1:
                return [SimpleNamespace(text="", sentences=[], tokens=[])]
            sentence = SimpleNamespace(
                tokens=[SimpleNamespace(confidence=0.8), SimpleNamespace(confidence=0.5)]
            )
            return [
                SimpleNamespace(
                    text=" Four score and seven years ago. ",
                    sentences=[sentence],
                    tokens=sentence.tokens,
                )
            ]

    def fake_load(model: str):
        model_loads.append(model)
        return FakeParakeet()

    def fake_logmel(waveform, _configuration):
        waveforms.append(np.array(waveform))
        return object()

    monkeypatch.setattr("radcliffe_media.speech.load_parakeet_model", fake_load)
    monkeypatch.setattr("radcliffe_media.speech.get_parakeet_logmel", fake_logmel)
    provider = ParakeetAppleSpeechProvider(
        parakeet_model="local-parakeet",
        chunk_duration=90,
        overlap_duration=10,
    )

    await provider.prepare()
    await provider.prepare()
    stereo_8khz = np.tile(np.array([16384, 16384], dtype="<i2"), 800).tobytes()
    transcription = await provider.transcribe_with_evidence(stereo_8khz, 8000, 2)

    assert model_loads == ["local-parakeet"]
    assert len(waveforms) == 2
    assert waveforms[0].shape == (16000,)
    assert np.count_nonzero(waveforms[0]) == 0
    assert waveforms[1].shape == (1600,)
    assert np.allclose(waveforms[1], 0.5)
    assert transcription.text == "Four score and seven years ago."
    assert len(transcription.segments) == 1
    assert transcription.segments[0].average_log_probability == pytest.approx(
        (math.log(0.8) + math.log(0.5)) / 2
    )
    assert assess_transcription(transcription).accepted


def test_parakeet_rejects_an_invalid_chunk_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        ParakeetAppleSpeechProvider(chunk_duration=10, overlap_duration=10)


@pytest.mark.asyncio
async def test_optional_verifier_decodes_the_identical_waveform(monkeypatch) -> None:
    calls: list[tuple[np.ndarray, dict[str, object]]] = []

    def fake_transcribe(audio: np.ndarray, **options):
        calls.append((audio.copy(), options))
        text = "Turn on the light." if len(calls) == 1 else "Turn off the light."
        return {"text": text, "segments": []}

    monkeypatch.setattr("radcliffe_media.speech.mlx_whisper.transcribe", fake_transcribe)
    provider = LocalAppleSpeechProvider(
        whisper_model="primary-model",
        verification_model="verification-model",
    )
    pcm = np.array([100, 200, 300], dtype="<i2").tobytes()

    transcription = await provider.transcribe_with_evidence(pcm, 16000, 1)

    assert transcription.text == "Turn on the light."
    assert transcription.alternative_texts == ("Turn off the light.",)
    assert np.array_equal(calls[0][0], calls[1][0])
    assert calls[0][1]["path_or_hf_repo"] == "primary-model"
    assert calls[1][1]["path_or_hf_repo"] == "verification-model"


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


def test_material_decoder_disagreement_is_not_silently_accepted() -> None:
    assessment = assess_transcription(
        Transcription(
            "Turn on the kitchen lights.",
            (TranscriptionSegmentEvidence(-0.4, 0.01, 0.5),),
            ("Turn off the kitchen lights.",),
        )
    )

    assert not assessment.accepted
    assert assessment.reason == "decoder_disagreement"
    assert assessment.maximum_hypothesis_word_error_rate is not None


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
